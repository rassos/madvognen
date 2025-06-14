import voluptuous as vol
import aiohttp
import logging

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Date format options
DATE_FORMAT_OPTIONS = {
    "iso": "2025-06-14 (ISO format)",
    "danish": "14/06/2025 (Danish format)",
    "danish_short": "14/6 (Short Danish)",
    "danish_text": "14. juni (Danish text)",
    "english": "June 14, 2025 (English)",
    "english_short": "Jun 14 (Short English)"
}

class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Madvognen."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self.customer_groups = []
        self.selected_group = None

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if user_input is not None:
            try:
                # Fetch customer groups
                self.customer_groups = await self._fetch_customer_groups()
                if not self.customer_groups:
                    return self.async_abort(reason="no_customer_groups")
                
                return await self.async_step_select_group()
            except Exception as e:
                _LOGGER.error("Error fetching customer groups: %s", e)
                return self.async_abort(reason="cannot_connect")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            description_placeholders={
                "description": "This will fetch available customer groups from Madvognen."
            }
        )

    async def async_step_select_group(self, user_input=None):
        """Handle customer group selection."""
        if user_input is not None:
            selected_id = user_input["customer_group"]
            self.selected_group = next(
                (group for group in self.customer_groups if group["id"] == selected_id),
                None
            )
            
            if self.selected_group is None:
                return self.async_abort(reason="invalid_group")
            
            return await self.async_step_date_format()

        # Create options for the dropdown
        group_options = {
            str(group["id"]): group["name"] 
            for group in self.customer_groups
        }

        return self.async_show_form(
            step_id="select_group",
            data_schema=vol.Schema({
                vol.Required("customer_group"): vol.In(group_options)
            }),
            description_placeholders={
                "count": str(len(self.customer_groups))
            }
        )

    async def async_step_date_format(self, user_input=None):
        """Handle date format selection."""
        if user_input is not None:
            # Create the config entry
            return self.async_create_entry(
                title=f"Madvognen - {self.selected_group['name']}",
                data={
                    "customer_group_id": self.selected_group["id"],
                    "customer_group_name": self.selected_group["name"],
                    "date_format": user_input["date_format"]
                }
            )

        return self.async_show_form(
            step_id="date_format",
            data_schema=vol.Schema({
                vol.Required("date_format", default="danish"): vol.In(DATE_FORMAT_OPTIONS)
            }),
            description_placeholders={
                "group_name": self.selected_group["name"]
            }
        )

    async def _fetch_customer_groups(self):
        """Fetch customer groups from Madvognen API."""
        url = "https://madvognen.dk/getservice.php?action=hentkundegrupper"
        
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status != 200:
                    raise CannotConnect()
                
                data = await response.json()
                
                if not isinstance(data, dict) or "kundegrupper" not in data:
                    _LOGGER.error("Invalid response format: %s", data)
                    raise InvalidData()
                
                groups = []
                for group_id, group_data in data["kundegrupper"].items():
                    if isinstance(group_data, dict) and "navn" in group_data:
                        groups.append({
                            "id": int(group_id),
                            "name": group_data["navn"]
                        })
                
                _LOGGER.debug("Fetched %d customer groups", len(groups))
                return sorted(groups, key=lambda x: x["name"])

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Madvognen."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_date_format = self.config_entry.options.get(
            "date_format", 
            self.config_entry.data.get("date_format", "danish")
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("date_format", default=current_date_format): vol.In(DATE_FORMAT_OPTIONS)
            })
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidData(HomeAssistantError):
    """Error to indicate invalid data was received."""
