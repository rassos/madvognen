import datetime
import pytz
import aiohttp
import async_timeout

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.event import async_track_time_change
from .const import DOMAIN, BASE_URL, CPH_TIMEZONE

class MadvognenWeeklyMenuSensor(Entity):
    def __init__(self, hass):
        self.hass = hass
        self._attr_name = "Madvognen Weekly Menu"
        self._attr_unique_id = "madvognen_weekly_menu"
        self._state = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self):
        """Set up the scheduled update every Sunday at 11:30."""
        async_track_time_change(
            self.hass,
            self._schedule_update,
            hour=11,
            minute=30,
            second=0
        )
        # Also update immediately when added
        await self.async_update()

    async def _schedule_update(self, now):
        await self.async_update()

    async def async_update(self):
        tz = pytz.timezone(CPH_TIMEZONE)
        today = datetime.datetime.now(tz).date()
        monday = today + datetime.timedelta(days=-today.weekday(), weeks=1)  # Next Monday

        week_data = {}

        async with aiohttp.ClientSession() as session:
            for day_offset in range(5):  # Monday to Friday
                day = monday + datetime.timedelta(days=day_offset)
                millis = self._calculate_millis(day)
                url = BASE_URL.format(millis=millis)

                try:
                    async with async_timeout.timeout(10):
                        async with session.get(url) as response:
                            if response.status == 200:
                                data = await response.json()
                                week_data[day.strftime("%A")] = self._parse_day_data(data)
                            else:
                                week_data[day.strftime("%A")] = ["Unavailable"]
                except Exception as e:
                    week_data[day.strftime("%A")] = [f"Error: {e}"]

        week_number = monday.isocalendar()[1]
        self._state = f"{monday.year}-Week-{week_number}"
        self._attr_extra_state_attributes = week_data

    def _calculate_millis(self, date_obj):
        tz = pytz.timezone(CPH_TIMEZONE)
        noon = datetime.datetime.combine(date_obj, datetime.time(12, 0))
        noon_cph = tz.localize(noon)
        noon_utc = noon_cph.astimezone(pytz.utc)
        epoch = datetime.datetime(1970, 1, 1, tzinfo=pytz.utc)
        return int((noon_utc - epoch).total_seconds() * 1000)

    def _parse_day_data(self, data):
        """Parse the dishes for a single day."""
        menuoverskrifter = data.get("menuoverskrifter", {})
        items = []
        for section in menuoverskrifter.values():
            for item in section.get("varer", []):
                items.append(item.get("Navn"))
        return items
