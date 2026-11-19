# Based on the veista/nilan integration.
# Original work Copyright veista (Taneli Veistinen), Apache License 2.0.
# See NOTICE and README.md for what changed.
"""The Nilan integration."""

from __future__ import annotations

import logging
from typing import Any

from modbus_connection import ModbusSerialParams, ModbusTcpParams
from modbus_connection.exceptions import ModbusError

from homeassistant.components.modbus.connection import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .const import DOMAIN
from .coordinator import NilanCoordinator
from .device import Device
from .register_cache import RegisterCache

PLATFORMS = [
    "binary_sensor",
    "button",
    "climate",
    "number",
    "select",
    "sensor",
    "switch",
    "water_heater",
]

_LOGGER = logging.getLogger(__name__)

SERIAL_LINE = {"baudrate": 19200, "bytesize": 8, "parity": "E", "stopbits": 1}
"""CTS602 serial line settings (the former integration used the same)."""


def connection_params(data: dict[str, Any]) -> ModbusTcpParams | ModbusSerialParams:
    """Build the connection parameters from a config entry's data.

    `com_type` keeps the values of the former integration: `tcp` is Modbus TCP to a
    gateway, `serial` and `serial_proxy` are Modbus RTU over a serial port, the
    latter one selected in Home Assistant (a serialx URL such as `esphome-hass://`).
    """
    if data["com_type"] == "tcp":
        return ModbusTcpParams(host=data["host_ip"], port=int(data["host_port"]))
    return ModbusSerialParams(device=data["host_port"], framer="rtu", **SERIAL_LINE)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Nilan CTS602 from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    name = entry.data["name"]
    unit_id = int(entry.data["unit_id"])

    try:
        unit = async_get_unit(hass, entry, connection_params(entry.data), unit_id)
    except HomeAssistantError as err:
        raise ConfigEntryNotReady(str(err)) from err

    cache = RegisterCache(unit)
    device = Device(hass, name, cache, unit_id)
    try:
        await device.setup()
    except (ValueError, ModbusError) as err:
        raise ConfigEntryNotReady(f"{name} did not answer: {err}") from err

    coordinator = NilanCoordinator(hass, entry, cache)
    device.coordinator = coordinator
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = device

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", config_entry.version)

    if config_entry.version == 1:
        new = {**config_entry.data}
        new.update({"com_type": "tcp"})
        new.update({"board_type": "CTS602"})
        hass.config_entries.async_update_entry(config_entry, data=new, version=3)

    elif config_entry.version == 2:
        new = {**config_entry.data}
        new.update({"board_type": "CTS602"})
        hass.config_entries.async_update_entry(config_entry, data=new, version=3)

    _LOGGER.info("Migration to version %s successful", config_entry.version)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry; the shared connection is released with the entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
