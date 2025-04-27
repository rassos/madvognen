import datetime
import pytz
import aiohttp
import async_timeout

from homeassistant.helpers.entity import Entity
from .const import DOMAIN, BASE_URL, CPH_TIMEZONE

async def async_setup_entry(hass, config_entry, async_add_entities):
    async_add_entities([MadvognenMenuSensor()], True)

class MadvognenMenuSensor(Entity):
    def __init__(self):
        self._attr_name = "Madvognen Menu"
        self._attr_unique_id = "madvognen"
        self._state = None
        self._attr_extra_state_attributes = {}

    async def async_update(self):
        millis = self._calculate_millis()
        url = BASE_URL.format(millis=millis)

        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status == 200:
                            data = await response.json()
                            self._parse_data(data)
                        else:
                            self._state = "Unavailable"
        except Exception as e:
            self._state = "Error"
            self._attr_extra_state_attributes["error"] = str(e)

    def _calculate_millis(self):
        tz = pytz.timezone(CPH_TIMEZONE)
        today = datetime.date.today()
        noon = datetime.datetime.combine(today, datetime.time(12, 0))
        noon_cph = tz.localize(noon)
        noon_utc = noon_cph.astimezone(pytz.utc)
        epoch = datetime.datetime(1970, 1, 1, tzinfo=pytz.utc)
        return int((noon_utc - epoch).total_seconds() * 1000)

    def _parse_data(self, data):
        """Simple parsing: set state to today's date, list dishes in attributes."""
        self._state = data.get("dato", "Unknown")
        menuoverskrifter = data.get("menuoverskrifter", {})

        items = []
        for section in menuoverskrifter.values():
            for item in section.get("varer", []):
                items.append(item.get("Navn"))

        self._attr_extra_state_attributes = {
            "dishes": items
        }
