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
        errors = {}
        
        if user_input is not None:
            try:
                # Fetch customer groups
                _LOGGER.debug("Starting to fetch customer groups...")
                self.customer_groups = await self._fetch_customer_groups()
                
                if not self.customer_groups:
                    _LOGGER.error("No customer groups returned from API")
                    errors["base"] = "no_customer_groups"
                else:
                    _LOGGER.debug("Successfully fetched %d groups, proceeding to selection", len(self.customer_groups))
                    return await self.async_step_select_group()
                    
            except CannotConnect as e:
                _LOGGER.error("Cannot connect to Madvognen API: %s", e)
                errors["base"] = "cannot_connect"
            except InvalidData as e:
                _LOGGER.error("Invalid data from Madvognen API: %s", e)
                errors["base"] = "invalid_data"
            except Exception as e:
                _LOGGER.error("Unexpected error during setup: %s", e, exc_info=True)
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
            errors=errors,
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
        url = "https://madvognen.dk/getservice.php?action=hentkundegruppe&kvikMenu=true"
        
        try:
            timeout = aiohttp.ClientTimeout(total=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                _LOGGER.debug("Fetching customer groups from: %s", url)
                async with session.get(url) as response:
                    _LOGGER.debug("Response status: %s", response.status)
                    
                    if response.status != 200:
                        _LOGGER.error("API returned status %s", response.status)
                        raise CannotConnect(f"API returned status {response.status}")
                    
                    content_type = response.headers.get('content-type', '')
                    _LOGGER.debug("Response content-type: %s", content_type)
                    
                    # Try to get raw text first for debugging
                    text_data = await response.text()
                    _LOGGER.debug("Raw response (first 500 chars): %s", text_data[:500])
                    
                    # Try to parse as JSON
                    try:
                        data = await response.json()
                    except Exception as json_error:
                        _LOGGER.error("Failed to parse JSON response: %s", json_error)
                        _LOGGER.error("Raw response: %s", text_data)
                        raise InvalidData(f"Invalid JSON response: {json_error}")
                    
                    _LOGGER.debug("Response type: %s", type(data))
                    _LOGGER.debug("Response data: %s", str(data)[:500])
                    
                    groups = []
                    
                    if isinstance(data, list):
                        # API returns a list of customer groups
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
                                        groups.append({
                                            "id": int(item[id_key]),
                                            "name": item[name_key]
                                        })
                                    except (ValueError, TypeError) as e:
                                        _LOGGER.warning("Skipping item with invalid ID/name: %s", e)
                                        continue
                                else:
                                    _LOGGER.debug("Item missing name or id keys: %s", list(item.keys()))
                            else:
                                _LOGGER.debug("Skipping non-dict item: %s", type(item))
                                
                    elif isinstance(data, dict):
                        # API returns a dictionary (fallback to old format)
                        _LOGGER.debug("Processing dictionary format")
                        
                        # Look for different possible container keys
                        possible_keys = ["kundegrupper", "customers", "groups", "customer_groups", "data"]
                        found_key = None
                        for key in possible_keys:
                            if key in data:
                                found_key = key
                                break
                        
                        if found_key:
                            kundegrupper = data[found_key]
                        else:
                            # Maybe the data itself contains the groups
                            kundegrupper = data
                        
                        if isinstance(kundegrupper, dict):
                            for group_id, group_data in kundegrupper.items():
                                if isinstance(group_data, dict) and "navn" in group_data:
                                    try:
                                        groups.append({
                                            "id": int(group_id),
                                            "name": group_data["navn"]
                                        })
                                    except ValueError as e:
                                        _LOGGER.warning("Skipping group with invalid ID '%s': %s", group_id, e)
                                        continue
                        elif isinstance(kundegrupper, list):
                            # Nested list in dictionary
                            for item in kundegrupper:
                                if isinstance(item, dict):
                                    # Use same logic as for direct list
                                    name_key = None
                                    id_key = None
                                    
                                    for key in ["navn", "name", "Navn", "Name"]:
                                        if key in item:
                                            name_key = key
                                            break
                                    
                                    for key in ["id", "ID", "Id", "kundegruppe_id", "KundegruppeID"]:
                                        if key in item:
                                            id_key = key
                                            break
                                    
                                    if name_key and id_key:
                                        try:
                                            groups.append({
                                                "id": int(item[id_key]),
                                                "name": item[name_key]
                                            })
                                        except (ValueError, TypeError) as e:
                                            _LOGGER.warning("Skipping item with invalid ID/name: %s", e)
                                            continue
                    else:
                        _LOGGER.error("Response is neither list nor dict: %s", type(data))
                        raise InvalidData(f"Expected list or dict, got {type(data)}")
                    
                    if not groups:
                        _LOGGER.error("No valid groups found in response")
                        raise InvalidData("No valid customer groups found")
                    
                    _LOGGER.debug("Successfully fetched %d customer groups", len(groups))
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
