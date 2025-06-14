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
        _LOGGER.debug("ConfigFlow initialized")

    async def async_step_user(self, user_input=None):
        """Handle the user step - auto-fetch groups and show combined form."""
        _LOGGER.debug("async_step_user called with input: %s", user_input)
        errors = {}
        
        # If user submitted the form, process it
        if user_input is not None:
            try:
                selected_id_str = user_input["customer_group"]
                date_format = user_input["date_format"]
                
                _LOGGER.debug("User selected ID: %s, date format: %s", selected_id_str, date_format)
                
                selected_id = int(selected_id_str)
                selected_group = next(
                    (group for group in self.customer_groups if group["id"] == selected_id),
                    None
                )
                
                if selected_group is None:
                    _LOGGER.error("Selected group not found. Looking for ID %s", selected_id)
                    errors["customer_group"] = "invalid_group"
                else:
                    _LOGGER.debug("Creating config entry for: %s", selected_group["name"])
                    
                    # Create the config entry immediately
                    return self.async_create_entry(
                        title=f"Madvognen - {selected_group['name']}",
                        data={
                            "customer_group_id": selected_group["id"],
                            "customer_group_name": selected_group["name"],
                            "date_format": date_format
                        }
                    )
                    
            except (ValueError, TypeError, KeyError) as e:
                _LOGGER.error("Error processing user input: %s", e)
                errors["customer_group"] = "invalid_group"

        # Always fetch customer groups if we don't have them (on first load or retry)
        if not self.customer_groups:
            try:
                _LOGGER.debug("Auto-fetching customer groups...")
                self.customer_groups = await self._fetch_customer_groups()
                _LOGGER.debug("Fetch completed. Got %d groups", len(self.customer_groups))
                
                if not self.customer_groups:
                    _LOGGER.error("No customer groups returned from API")
                    errors["base"] = "no_customer_groups"
                    
            except CannotConnect as e:
                _LOGGER.error("Cannot connect to Madvognen API: %s", e)
                errors["base"] = "cannot_connect"
            except InvalidData as e:
                _LOGGER.error("Invalid data from Madvognen API: %s", e)
                errors["base"] = "invalid_data"
            except Exception as e:
                _LOGGER.error("Unexpected error during setup: %s", e, exc_info=True)
                errors["base"] = "unknown"

        # Show the form
        if self.customer_groups and not errors:
            # Create options for the dropdown
            group_options = {}
            for group in self.customer_groups:
                group_id_str = str(group["id"])
                group_name = group["name"]
                group_options[group_id_str] = group_name

            _LOGGER.debug("Showing form with %d group options", len(group_options))
            
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("customer_group"): vol.In(group_options),
                    vol.Required("date_format", default="danish"): vol.In(DATE_FORMAT_OPTIONS)
                }),
                errors=errors,
                description_placeholders={
                    "count": str(len(self.customer_groups))
                }
            )
        else:
            # Show error form if groups failed to load
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({}),
                errors=errors,
                description_placeholders={
                    "description": "Failed to load customer groups. Please try again."
                }
            )

    async def _fetch_customer_groups(self):
        """Fetch customer groups from Madvognen API."""
        url = "https://madvognen.dk/getservice.php?action=hentkundegruppe&kvikMenu=true"
        
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                _LOGGER.debug("Making request to: %s", url)
                async with session.get(url) as response:
                    _LOGGER.debug("Got response with status: %s", response.status)
                    
                    if response.status != 200:
                        _LOGGER.error("API returned status %s", response.status)
                        raise CannotConnect(f"API returned status {response.status}")
                    
                    # Try to parse as JSON
                    try:
                        data = await response.json()
                        _LOGGER.debug("JSON parsing successful. Response type: %s", type(data))
                    except Exception as json_error:
                        _LOGGER.error("Failed to parse JSON response: %s", json_error)
                        raise InvalidData(f"Invalid JSON response: {json_error}")
                    
                    groups = []
                    
                    if isinstance(data, list):
                        _LOGGER.debug("Processing list of %d items", len(data))
                        for item in data:
                            if isinstance(item, dict):
                                # Check for different possible key names
                                name_key = None
                                id_key = None
                                
                                # Common variations for name
                                for key in ["navn", "name", "Navn", "Name"]:
                                    if key in item:
                                        name_key = key
                                        break
                                
                                # Common variations for ID
                                for key in ["id", "ID", "Id", "kundegruppe_id", "KundegruppeID"]:
                                    if key in item:
                                        id_key = key
                                        break
                                
                                if name_key and id_key:
                                    try:
                                        group_id = int(item[id_key])
                                        group_name = str(item[name_key]).strip()
                                        
                                        if group_name and group_id:
                                            groups.append({
                                                "id": group_id,
                                                "name": group_name
                                            })
                                    except (ValueError, TypeError) as e:
                                        _LOGGER.warning("Skipping item with invalid ID/name: %s", e)
                                        continue
                    else:
                        _LOGGER.error("Expected list but got %s", type(data))
                        raise InvalidData(f"Expected list, got {type(data)}")
                    
                    if not groups:
                        _LOGGER.error("No valid groups found in response")
                        raise InvalidData("No valid customer groups found")
                    
                    _LOGGER.debug("Successfully processed %d customer groups", len(groups))
                    return sorted(groups, key=lambda x: x["name"])
                    
        except aiohttp.ClientError as e:
            _LOGGER.error("Network error fetching customer groups: %s", e)
            raise CannotConnect(f"Network error: {e}")
        except Exception as e:
            _LOGGER.error("Unexpected error fetching customer groups: %s", e, exc_info=True)
            raise CannotConnect(f"Unexpected error: {e}")

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
