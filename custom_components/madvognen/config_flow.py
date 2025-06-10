import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
import aiohttp
import async_timeout
import logging

from .const import DOMAIN, BASE_URL

_LOGGER = logging.getLogger(__name__)

class MadvognenConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Madvognen."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._customer_groups = {}

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}

        # Fetch customer groups on first load
        if not self._customer_groups:
            try:
                self._customer_groups = await self._fetch_customer_groups()
            except Exception as e:
                _LOGGER.error("Failed to fetch customer groups: %s", e)
                errors["base"] = "cannot_connect"

        if user_input is not None:
            # Get the selected customer group info
            selected_name = user_input["customer_group"]
            customer_group_id = self._customer_groups.get(selected_name)
            
            if customer_group_id and await self._test_api_connection(customer_group_id):
                return self.async_create_entry(
                    title=f"Madvognen - {selected_name}",
                    data={
                        "customer_group_id": customer_group_id,
                        "customer_group_name": selected_name
                    }
                )
            else:
                errors["customer_group"] = "invalid_customer_group"

        # Create dropdown options from customer groups
        if self._customer_groups:
            customer_group_options = list(self._customer_groups.keys())
            default_selection = customer_group_options[0] if customer_group_options else None
        else:
            customer_group_options = []
            default_selection = None

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required("customer_group", default=default_selection): vol.In(customer_group_options),
            }),
            errors=errors,
        )

    async def _fetch_customer_groups(self):
        """Fetch available customer groups from Madvognen API."""
        customer_groups = {}
        
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = "https://madvognen.dk/getservice.php?action=hentkundegruppe&kvikMenu=true"
                async with session.get(url) as response:
                    if response.status == 200:
                        # Set encoding to handle Danish characters properly
                        text = await response.text(encoding='utf-8')
                        data = await response.json(encoding='utf-8')
                        
                        if isinstance(data, list):
                            for group in data:
                                if isinstance(group, dict) and "Navn" in group and "KundegruppeID" in group:
                                    name = group["Navn"].strip()
                                    group_id = group["KundegruppeID"]
                                    if name and group_id:
                                        customer_groups[name] = int(group_id)
                        
                        _LOGGER.debug("Fetched %d customer groups", len(customer_groups))
                        return customer_groups
                    else:
                        _LOGGER.error("Failed to fetch customer groups: HTTP %s", response.status)
                        return {}
                        
        except Exception as e:
            _LOGGER.error("Error fetching customer groups: %s", e)
            raise

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
                    
        except Exception as e:
            _LOGGER.error("API connection test failed: %s", e)
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
        self._customer_groups = {}

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        errors = {}
        
        # Fetch customer groups on first load
        if not self._customer_groups:
            try:
                self._customer_groups = await self._fetch_customer_groups()
            except Exception as e:
                _LOGGER.error("Failed to fetch customer groups in options: %s", e)
                errors["base"] = "cannot_connect"

        if user_input is not None:
            # Get the selected customer group info
            selected_name = user_input["customer_group"]
            customer_group_id = self._customer_groups.get(selected_name)
            
            return self.async_create_entry(
                title="", 
                data={
                    "customer_group_id": customer_group_id,
                    "customer_group_name": selected_name
                }
            )

        # Get current selection
        current_name = self.config_entry.data.get("customer_group_name")
        customer_group_options = list(self._customer_groups.keys()) if self._customer_groups else []
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required("customer_group", default=current_name): vol.In(customer_group_options),
            }),
            errors=errors,
        )

    async def _fetch_customer_groups(self):
        """Fetch available customer groups from Madvognen API."""
        customer_groups = {}
        
        try:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                url = "https://madvognen.dk/getservice.php?action=hentkundegruppe&kvikMenu=true"
                async with session.get(url) as response:
                    if response.status == 200:
                        # Handle Danish characters properly
                        data = await response.json(encoding='utf-8')
                        
                        if isinstance(data, list):
                            for group in data:
                                if isinstance(group, dict) and "Navn" in group and "KundegruppeID" in group:
                                    name = group["Navn"].strip()
                                    group_id = group["KundegruppeID"]
                                    if name and group_id:
                                        customer_groups[name] = int(group_id)
                        
                        _LOGGER.debug("Fetched %d customer groups for options", len(customer_groups))
                        return customer_groups
                    else:
                        _LOGGER.error("Failed to fetch customer groups: HTTP %s", response.status)
                        return {}
                        
        except Exception as e:
            _LOGGER.error("Error fetching customer groups: %s", e)
            raise
