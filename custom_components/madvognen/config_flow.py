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
                    
                    # Get response text for debugging
                    text_data = await response.text()
                    _LOGGER.debug("Raw response (first 500 chars): %s", text_data[:500])
                    
                    # Parse JSON
                    try:
                        data = await response.json()
                        _LOGGER.debug("JSON parsed successfully. Type: %s", type(data))
                    except Exception as json_error:
                        _LOGGER.error("Failed to parse JSON response: %s", json_error)
                        raise InvalidData(f"Invalid JSON response: {json_error}")
                    
                    groups = []
                    
                    # Handle list format (what the API actually returns)
                    if isinstance(data, list):
                        _LOGGER.debug("Processing list of %d items", len(data))
                        
                        for item in data:
                            if not isinstance(item, dict):
                                _LOGGER.debug("Skipping non-dict item: %s", type(item))
                                continue
                            
                            # Look for name and ID fields with various possible keys
                            name = None
                            item_id = None
                            
                            # Try different name fields
                            for name_key in ["navn", "name", "Navn", "Name", "title", "label"]:
                                if name_key in item and item[name_key]:
                                    name = item[name_key]
                                    break
                            
                            # Try different ID fields
                            for id_key in ["id", "ID", "Id", "kundegruppe_id", "KundegruppeID", "value"]:
                                if id_key in item and item[id_key] is not None:
                                    try:
                                        item_id = int(item[id_key])
                                        break
                                    except (ValueError, TypeError):
                                        continue
                            
                            # If we found both name and ID, add the group
                            if name and item_id is not None:
                                groups.append({
                                    "id": item_id,
                                    "name": str(name).strip()
                                })
                                _LOGGER.debug("Added group: ID=%s, Name=%s", item_id, name)
                            else:
                                _LOGGER.debug("Skipping item missing name or ID. Keys: %s", list(item.keys()))
                                if item:
                                    _LOGGER.debug("Item content: %s", item)
                    
                    # Handle dict format (fallback)
                    elif isinstance(data, dict):
                        _LOGGER.debug("Processing dict format")
                        
                        # Look for groups in various dict structures
                        groups_data = None
                        for key in ["kundegrupper", "customers", "groups", "customer_groups", "data", "items"]:
                            if key in data:
                                groups_data = data[key]
                                _LOGGER.debug("Found groups in key: %s", key)
                                break
                        
                        if groups_data is None:
                            # Maybe the dict itself contains the groups
                            groups_data = data
                        
                        if isinstance(groups_data, dict):
                            # Dict of groups
                            for group_id, group_data in groups_data.items():
                                if isinstance(group_data, dict):
                                    name = group_data.get("navn") or group_data.get("name")
                                    if name:
                                        try:
                                            groups.append({
                                                "id": int(group_id),
                                                "name": str(name).strip()
                                            })
                                        except (ValueError, TypeError):
                                            _LOGGER.debug("Skipping group with invalid ID: %s", group_id)
                        
                        elif isinstance(groups_data, list):
                            # List within dict - use same logic as direct list
                            for item in groups_data:
                                if isinstance(item, dict):
                                    name = None
                                    item_id = None
                                    
                                    for name_key in ["navn", "name", "Navn", "Name"]:
                                        if name_key in item and item[name_key]:
                                            name = item[name_key]
                                            break
                                    
                                    for id_key in ["id", "ID", "Id", "kundegruppe_id", "KundegruppeID"]:
                                        if id_key in item and item[id_key] is not None:
                                            try:
                                                item_id = int(item[id_key])
                                                break
                                            except (ValueError, TypeError):
                                                continue
                                    
                                    if name and item_id is not None:
                                        groups.append({
                                            "id": item_id,
                                            "name": str(name).strip()
                                        })
                    
                    else:
                        _LOGGER.error("Response is neither list nor dict: %s", type(data))
                        raise InvalidData(f"Unexpected response type: {type(data)}")
                    
                    if not groups:
                        _LOGGER.error("No valid groups found in response")
                        _LOGGER.debug("Response data: %s", data)
                        raise InvalidData("No valid customer groups found in API response")
                    
                    _LOGGER.debug("Successfully parsed %d customer groups", len(groups))
                    return sorted(groups, key=lambda x: x["name"])
                    
        except aiohttp.ClientError as e:
            _LOGGER.error("Network error fetching customer groups: %s", e)
            raise CannotConnect(f"Network error: {e}")
        except (CannotConnect, InvalidData):
            # Re-raise these specific exceptions
            raise
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
