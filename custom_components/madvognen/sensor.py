import datetime
import logging
import pytz
import aiohttp
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.const import STATE_UNAVAILABLE
from .const import DOMAIN, BASE_URL, CPH_TIMEZONE

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Madvognen sensor platform."""
    
    # Create and add the sensor entity
    sensor = MadvognenWeeklyMenuSensor(hass, config_entry)
    async_add_entities([sensor], True)

class MadvognenWeeklyMenuSensor(Entity):
    def __init__(self, hass, config_entry):
        self.hass = hass
        self._config_entry = config_entry
        
        # Get customer group name for a more descriptive sensor name
        customer_group_name = config_entry.data.get("customer_group_name", "Unknown")
        self._attr_name = f"Madvognen Menu - {customer_group_name}"
        self._attr_unique_id = f"madvognen_weekly_menu_{config_entry.data.get('customer_group_id', 252)}"
        self._state = None
        self._attr_extra_state_attributes = {}
        self._available = True

    @property
    def available(self):
        """Return if entity is available."""
        return self._available

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def icon(self):
        """Return the icon to use in the frontend."""
        return "mdi:food"

    async def async_added_to_hass(self):
        """Set up the scheduled update."""
        # Update every day at 6 AM to get fresh data
        async_track_time_change(
            self.hass,
            self._schedule_update,
            hour=6,
            minute=0,
            second=0
        )
        # Also update immediately when added
        await self.async_update()

    async def _schedule_update(self, now):
        """Called by the time tracking."""
        await self.async_update()

    async def async_update(self):
        """Fetch the weekly menu data."""
        try:
            tz = pytz.timezone(CPH_TIMEZONE)
            today = datetime.datetime.now(tz).date()
            
            # Get current week's menu (Monday to Friday)
            # If it's weekend, get next week's menu
            if today.weekday() >= 5:  # Saturday or Sunday
                monday = today + datetime.timedelta(days=-today.weekday(), weeks=1)
            else:
                monday = today - datetime.timedelta(days=today.weekday())

            _LOGGER.debug("Fetching menu for week starting %s", monday)
            
            week_data = await self._fetch_week_data(monday)
            
            if week_data:
                week_number = monday.isocalendar()[1]
                self._state = f"{monday.year}-W{week_number:02d}"
                self._attr_extra_state_attributes = {
                    "week_start": monday.isoformat(),
                    "last_updated": datetime.datetime.now(tz).isoformat(),
                    **week_data
                }
                self._available = True
                _LOGGER.debug("Successfully updated menu data")
            else:
                self._available = False
                _LOGGER.warning("Failed to fetch menu data")

        except Exception as e:
            _LOGGER.error("Error updating madvognen menu: %s", e)
            self._available = False

    async def _fetch_week_data(self, monday):
        """Fetch menu data for a full week."""
        week_data = {}
        
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for day_offset in range(5):  # Monday to Friday
                day = monday + datetime.timedelta(days=day_offset)
                day_name = day.strftime("%A")
                
                try:
                    menu_items = await self._fetch_day_menu(session, day)
                    week_data[day_name.lower()] = {
                        "date": day.isoformat(),
                        "items": menu_items,
                        "available": len(menu_items) > 0
                    }
                    _LOGGER.debug("Fetched %d items for %s", len(menu_items), day_name)
                    
                except Exception as e:
                    _LOGGER.warning("Failed to fetch menu for %s: %s", day_name, e)
                    week_data[day_name.lower()] = {
                        "date": day.isoformat(),
                        "items": [],
                        "available": False,
                        "error": str(e)
                    }

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
            return self._parse_day_data(data)

    def _calculate_millis(self, date_obj):
        """Calculate milliseconds since epoch for noon on given date in Copenhagen timezone."""
        tz = pytz.timezone(CPH_TIMEZONE)
        noon = datetime.datetime.combine(date_obj, datetime.time(12, 0))
        noon_cph = tz.localize(noon)
        noon_utc = noon_cph.astimezone(pytz.utc)
        epoch = datetime.datetime(1970, 1, 1, tzinfo=pytz.utc)
        return int((noon_utc - epoch).total_seconds() * 1000)

    def _parse_day_data(self, data):
        """Parse the dishes for a single day."""
        if not isinstance(data, dict):
            _LOGGER.warning("Invalid data format received")
            return []
            
        menuoverskrifter = data.get("menuoverskrifter", {})
        if not menuoverskrifter:
            _LOGGER.debug("No menu sections found in data")
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
                        
        _LOGGER.debug("Parsed %d menu items", len(items))
        return items
