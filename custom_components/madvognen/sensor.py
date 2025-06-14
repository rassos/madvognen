import asyncio
import datetime
import logging
from zoneinfo import ZoneInfo
import aiohttp

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://madvognen.dk/getservice.php?action=hentmenukundegruppe&KundegruppeID=252&millis={millis}"
CPH_TIMEZONE = "Europe/Copenhagen"

# Danish month names for text formatting
DANISH_MONTHS = {
    1: "januar", 2: "februar", 3: "marts", 4: "april",
    5: "maj", 6: "juni", 7: "juli", 8: "august", 
    9: "september", 10: "oktober", 11: "november", 12: "december"
}

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Madvognen sensor from a config entry."""
    sensor = MadvognenWeeklyMenuSensor(config_entry)
    async_add_entities([sensor], True)
    
    # Listen for options updates
    async def async_update_options(hass, config_entry):
        """Update options."""
        await hass.config_entries.async_reload(config_entry.entry_id)
    
    config_entry.async_on_unload(
        config_entry.add_update_listener(async_update_options)
    )


class MadvognenWeeklyMenuSensor(SensorEntity):
    """Sensor for Madvognen weekly menu."""

    def __init__(self, config_entry: ConfigEntry):
        """Initialize the sensor."""
        self._config_entry = config_entry
        customer_group_name = config_entry.data.get("customer_group_name", "Unknown")
        self._attr_name = f"Madvognen Menu {customer_group_name}"
        self._attr_unique_id = f"madvognen_menu_{customer_group_name.lower().replace(' ', '_')}"
        self._attr_icon = "mdi:food"
        self._state = None
        self._attr_extra_state_attributes = {}
        
        # Get date format preference
        self._date_format = config_entry.options.get(
            "date_format", 
            config_entry.data.get("date_format", "danish")
        )

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    async def async_update(self):
        """Update the sensor."""
        try:
            # Get current time in Copenhagen timezone
            tz = ZoneInfo(CPH_TIMEZONE)
            now = dt_util.now().astimezone(tz)
            current_date = now.date()
            current_time = now.time()
            
            # Smart week selection based on day and time
            monday = self._get_relevant_monday(current_date, current_time)
            
            _LOGGER.debug("Fetching menu for week starting %s (today is %s)", monday, current_date)
            
            # Fetch menu data
            week_data = await self._fetch_week_data(monday)
            
            if week_data:
                self._attr_extra_state_attributes.update(week_data)
                self._attr_extra_state_attributes["last_updated"] = now.isoformat()
                
                # Create a more informative state
                week_info = self._get_week_description(monday, current_date)
                self._state = week_info
                _LOGGER.debug("Successfully updated menu data for %s", week_info)
            else:
                # Don't clear existing data on failure, just update the last_updated
                # Only clear if it's a fresh start with no data
                if not hasattr(self, '_state') or self._state is None:
                    self._state = "unavailable"
                    self._attr_extra_state_attributes = {}
                else:
                    # Keep existing data but update timestamp to show we tried
                    self._attr_extra_state_attributes["last_updated"] = now.isoformat()
                    self._attr_extra_state_attributes["last_error"] = "Failed to fetch menu data"
                _LOGGER.warning("Failed to fetch menu data")
                
        except Exception as e:
            # Keep existing data on error, just log it
            _LOGGER.error("Error updating Madvognen sensor: %s", e)
            if not hasattr(self, '_state') or self._state is None:
                self._state = "unavailable"
                self._attr_extra_state_attributes = {}

    async def _fetch_week_data(self, monday):
        """Fetch menu data for a full week."""
        week_data = {}
        previous_menu = None
        
        # Add rate limiting to prevent HTTP 403 errors
        timeout = aiohttp.ClientTimeout(total=30)
        connector = aiohttp.TCPConnector(limit=1)  # Limit concurrent connections
        
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
            for day_offset in range(5):  # Monday to Friday
                day = monday + datetime.timedelta(days=day_offset)
                day_name = day.strftime("%A")
                
                try:
                    menu_items = await self._fetch_day_menu(session, day)
                    
                    # Check if this menu is identical to the previous day
                    # If so, it might be a fallback response from the API
                    if menu_items and previous_menu and menu_items == previous_menu:
                        _LOGGER.warning("Menu for %s is identical to previous day - might be API fallback", day_name)
                        # For now, we'll still include it, but mark it as potentially incorrect
                        week_data[day_name.lower()] = {
                            "date": day.isoformat(),
                            "items": menu_items,
                            "available": True,
                            "note": "Might be repeated from previous day"
                        }
                    elif menu_items:
                        week_data[day_name.lower()] = {
                            "date": self._format_date(day),
                            "date_iso": day.isoformat(),  # Keep ISO for internal use
                            "items": menu_items,
                            "available": True
                        }
                        previous_menu = menu_items.copy()  # Store for comparison
                    else:
                        week_data[day_name.lower()] = {
                            "date": self._format_date(day),
                            "date_iso": day.isoformat(),
                            "items": [],
                            "available": False
                        }
                    
                    _LOGGER.debug("Fetched %d items for %s", len(menu_items), day_name)
                    
                    # Add delay between requests to prevent rate limiting
                    if day_offset < 4:  # Don't delay after the last request
                        await asyncio.sleep(1)
                    
                except Exception as e:
                    _LOGGER.warning("Failed to fetch menu for %s: %s", day_name, e)
                    week_data[day_name.lower()] = {
                        "date": self._format_date(day),
                        "date_iso": day.isoformat(),
                        "items": [],
                        "available": False,
                        "error": str(e)
                    }
                    
                    # Add delay even on error to prevent rapid retries
                    if day_offset < 4:
                        await asyncio.sleep(1)

        return week_data if any(day["available"] for day in week_data.values()) else None

    async def _fetch_day_menu(self, session, date_obj):
        """Fetch menu for a specific day."""
        millis = self._calculate_millis(date_obj)
        
        # Get customer group ID from config, default to 252
        customer_group_id = self._config_entry.data.get("customer_group_id", 252)
        url = BASE_URL.format(millis=millis).replace("252", str(customer_group_id))
        
        async with session.get(url) as response:
            if response.status != 200:
                raise Exception(f"HTTP {response.status}")
                
            data = await response.json()
            
            # Check if the returned data is actually for the requested date
            # If API returns data for a different date, we should return empty
            menu_items = self._parse_day_data(data, date_obj)
            return menu_items

    def _get_week_description(self, monday, current_date):
        """Get a human-readable description of which week we're showing."""
        days_since_monday = current_date.weekday()
        current_week_monday = current_date - datetime.timedelta(days=days_since_monday)
        
        formatted_date = self._format_date(monday)
        
        if monday == current_week_monday:
            return f"This week ({formatted_date})"
        else:
            return f"Next week ({formatted_date})"

    def _format_date(self, date_obj):
        """Format date according to user preference."""
        if self._date_format == "iso":
            return date_obj.strftime("%Y-%m-%d")
        elif self._date_format == "danish":
            return date_obj.strftime("%d/%m/%Y")
        elif self._date_format == "danish_short":
            return date_obj.strftime("%d/%m")
        elif self._date_format == "danish_text":
            month_name = DANISH_MONTHS[date_obj.month]
            return f"{date_obj.day}. {month_name}"
        elif self._date_format == "english":
            return date_obj.strftime("%B %d, %Y")
        elif self._date_format == "english_short":
            return date_obj.strftime("%b %d")
        else:
            # Default to Danish format
            return date_obj.strftime("%d/%m/%Y")

    def _get_relevant_monday(self, current_date, current_time):
        """Get the Monday of the week we should show the menu for.
        
        Logic:
        - Monday-Thursday: Show current week
        - Friday after 14:00 (2 PM): Show next week (weekend prep)
        - Friday before 14:00: Show current week
        - Saturday-Sunday: Show next week
        """
        days_since_monday = current_date.weekday()
        current_monday = current_date - datetime.timedelta(days=days_since_monday)
        next_monday = current_monday + datetime.timedelta(days=7)
        
        weekday = current_date.weekday()  # 0=Monday, 6=Sunday
        
        if weekday <= 3:  # Monday to Thursday
            return current_monday
        elif weekday == 4:  # Friday
            # After 2 PM on Friday, show next week
            if current_time >= datetime.time(14, 0):
                return next_monday
            else:
                return current_monday
        else:  # Saturday (5) or Sunday (6)
            return next_monday

    def _calculate_millis(self, date_obj):
        """Calculate milliseconds since epoch for noon on given date in Copenhagen timezone."""
        tz = ZoneInfo(CPH_TIMEZONE)
        noon = datetime.datetime.combine(date_obj, datetime.time(12, 0), tzinfo=tz)
        epoch = datetime.datetime(1970, 1, 1, tzinfo=ZoneInfo('UTC'))
        return int((noon - epoch).total_seconds() * 1000)

    def _parse_day_data(self, data, requested_date):
        """Parse the dishes for a single day and validate the date."""
        if not isinstance(data, dict):
            _LOGGER.warning("Invalid data format received")
            return []
        
        # Check if the returned date matches our request
        returned_date = data.get("dato")
        requested_date_str = requested_date.strftime("%Y-%m-%d")
        
        if returned_date != requested_date_str:
            _LOGGER.info("API returned date %s but we requested %s - no menu available for requested date", 
                        returned_date, requested_date_str)
            return []
            
        menuoverskrifter = data.get("menuoverskrifter", {})
        if not menuoverskrifter:
            _LOGGER.debug("No menu sections found in data for %s", requested_date)
            return []
        
        # Check if any menu items exist
        total_items = 0
        for section in menuoverskrifter.values():
            if isinstance(section, dict):
                varer = section.get("varer", [])
                total_items += len(varer)
        
        # If no items at all, this day probably has no menu
        if total_items == 0:
            _LOGGER.debug("No menu items found for %s", requested_date)
            return []
            
        items = []
        for section_name, section in menuoverskrifter.items():
            if not isinstance(section, dict):
                continue
                
            varer = section.get("varer", [])
            for item in varer:
                if isinstance(item, dict) and "Navn" in item:
                    name = item["Navn"]
                    if name and name.strip():
                        items.append(name.strip())
        
        _LOGGER.debug("Found %d menu items for %s (API date: %s)", len(items), requested_date, returned_date)
        return items
