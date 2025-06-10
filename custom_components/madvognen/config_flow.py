import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import aiohttp
import async_timeout

from .const import DOMAIN, BASE_URL

class MadvognenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Madvognen."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            # Validate the customer group ID by testing the API
            customer_group_id = user_input["customer_group_id"]
            
            if await self._test_api_connection(customer_group_id):
                return self.async_create_entry(
                    title=f"Madvognen (Group {customer_group_id})",
                    data=user_input
                )
            else:
                errors["customer_group_id"] = "invalid_customer_group"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("customer_group_id", default=252): int,
            }),
            errors=errors,
        )

    async def _test_api_connection(self, customer_group_id):
        """Test if we can connect to the API with the given customer group ID."""
        try:
            # Use current timestamp for testing
            import datetime
            import pytz
            
            tz = pytz.timezone("Europe/Copenhagen")
            now = datetime.datetime.now(tz)
            noon = datetime.datetime.combine(now.date(), datetime.time(12, 0))
            noon_cph = tz.localize(noon)
            noon_utc = noon_cph.astimezone(pytz.utc)
            epoch = datetime.datetime(1970, 1, 1, tzinfo=pytz.utc)
            millis = int((noon_utc - epoch).total_seconds() * 1000)
            
            url = BASE_URL.format(millis=millis).replace("252", str(customer_group_id))
            
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        # Check if we got valid menu data structure
                        return isinstance(data, dict) and "menuoverskrifter" in data
                    return False
                    
        except Exception:
            return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return MadvognenOptionsFlowHandler(config_entry)


class MadvognenOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Madvognen."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(
                    "customer_group_id",
                    default=self.config_entry.data.get("customer_group_id", 252)
                ): int,
            })
        )
