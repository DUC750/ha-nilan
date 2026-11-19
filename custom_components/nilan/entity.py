"""Base entity of the Nilan integration."""

from __future__ import annotations

import logging

from modbus_connection.exceptions import ModbusError

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NilanCoordinator
from .device import Device

_LOGGER = logging.getLogger(__name__)


class NilanEntity(CoordinatorEntity[NilanCoordinator]):
    """Nilan entity: decodes its state from the coordinator's register snapshot."""

    def __init__(self, device: Device) -> None:
        """Initialize the instance."""
        super().__init__(device.coordinator)
        self._device = device

    async def async_read_state(self) -> None:
        """Decode the entity state from the register snapshot (no bus I/O)."""

    async def async_update(self) -> None:
        """Decode from the snapshot.

        Runs before the entity is added (`update_before_add`, as in the former
        integration, so every attribute exists before the first state write) and on
        `homeassistant.update_entity`. Polling is off; the coordinator drives updates.
        """
        try:
            await self.async_read_state()
        except ModbusError as err:
            # The link dropped; the coordinator marks the entity unavailable and the
            # next refresh decodes it again.
            _LOGGER.debug("Decoding %s failed: %s", self.entity_id, err)

    async def async_added_to_hass(self) -> None:
        """Follow every coordinator refresh."""
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_reader(self.async_read_state))

    @property
    def device_info(self):
        """Device Info."""
        unique_id = self._device.get_device_name + self._device.get_device_type

        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, unique_id),
            },
            "name": self._device.get_device_name,
            "manufacturer": "Nilan",
            "model": self._device.get_device_type,
            "sw_version": self._device.get_device_sw_version,
            "hw_version": str(self._device.get_device_hw_version),
            "suggested_area": "Boiler Room",
        }
