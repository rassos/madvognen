"""Support for SFOWeb appointments sensor."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)

from .const import DOMAIN
from .scraper import SFOScraper

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SFOWeb sensor based on a config entry."""
    
    try:
        _LOGGER.info("Setting up SFOWeb sensors...")
        
        # Get the scraper instance from the domain data
        scraper = hass.data[DOMAIN][config_entry.entry_id]["scraper"]
        
        # Create data update coordinator
        coordinator = SFOWebDataUpdateCoordinator(hass, scraper)
        
        # Fetch initial data
        await coordinator.async_config_entry_first_refresh()
        
        # Store coordinator in domain data
        hass.data[DOMAIN][config_entry.entry_id]["coordinator"] = coordinator
        
        # Create sensor entities
        sensors = [
            SFOWebAppointmentsSensor(coordinator, config_entry),
            SFOWebNextAppointmentSensor(coordinator, config_entry),
        ]
        
        async_add_entities(sensors, True)
        _LOGGER.info("SFOWeb sensors successfully set up")
        
    except Exception as e:
        _LOGGER.error(f"Error setting up SFOWeb sensors: {e}")
        raise


class SFOWebDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching SFOWeb appointment data."""

    def __init__(self, hass: HomeAssistant, scraper: SFOScraper) -> None:
        """Initialize the coordinator."""
        self.scraper = scraper
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> List[Dict[str, Any]]:
        """Fetch data from SFOWeb."""
        try:
            _LOGGER.debug("Fetching appointments data from SFOWeb...")
            appointments = await self.scraper.async_get_appointments()
            _LOGGER.info(f"Successfully fetched {len(appointments)} appointments")
            return appointments
        except Exception as err:
            _LOGGER.error(f"Error fetching appointments: {err}")
            raise UpdateFailed(f"Error communicating with SFOWeb: {err}") from err


class SFOWebAppointmentsSensor(CoordinatorEntity, SensorEntity):
    """Sensor for all SFOWeb appointments."""

    def __init__(
        self, 
        coordinator: SFOWebDataUpdateCoordinator, 
        config_entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = "SFOWeb Appointments"
        self._attr_unique_id = f"{config_entry.entry_id}_appointments"
        self._attr_icon = "mdi:calendar-multiple"

    @property
    def native_value(self) -> int:
        """Return the number of appointments."""
        if self.coordinator.data is None:
            return 0
        return len(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        if self.coordinator.data is None:
            return {}
        
        attributes = {
            "appointments": [],
            "last_updated": getattr(self.coordinator, 'last_update_success_time', None) or self.coordinator.last_update_success,
        }
        
        # Add appointment details
        for i, appointment in enumerate(self.coordinator.data):
            attributes["appointments"].append({
                "date": appointment.get("date", ""),
                "what": appointment.get("what", ""),
                "time": appointment.get("time", ""),
                "comment": appointment.get("comment", ""),
                "description": appointment.get("full_description", ""),
            })
        
        return attributes

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success


class SFOWebNextAppointmentSensor(CoordinatorEntity, SensorEntity):
    """Sensor for the next SFOWeb appointment."""

    def __init__(
        self, 
        coordinator: SFOWebDataUpdateCoordinator, 
        config_entry: ConfigEntry
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._attr_name = "SFOWeb Next Appointment"
        self._attr_unique_id = f"{config_entry.entry_id}_next_appointment"
        self._attr_icon = "mdi:calendar-clock"

    @property
    def native_value(self) -> str:
        """Return the next appointment description."""
        if self.coordinator.data is None or len(self.coordinator.data) == 0:
            return "No appointments"
        
        # Get the first appointment (assuming they're sorted by date)
        next_appointment = self.coordinator.data[0]
        return next_appointment.get("full_description", "No description")

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return additional state attributes."""
        if self.coordinator.data is None or len(self.coordinator.data) == 0:
            return {"last_updated": getattr(self.coordinator, 'last_update_success_time', None) or self.coordinator.last_update_success}
        
        next_appointment = self.coordinator.data[0]
        
        return {
            "date": next_appointment.get("date", ""),
            "what": next_appointment.get("what", ""),
            "time": next_appointment.get("time", ""),
            "comment": next_appointment.get("comment", ""),
            "total_appointments": len(self.coordinator.data),
            "last_updated": getattr(self.coordinator, 'last_update_success_time', None) or self.coordinator.last_update_success,
        }

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success
