"""Coordinator and entity behaviour inside Home Assistant."""

from __future__ import annotations

from datetime import timedelta

from modbus_connection import ModbusSerialParams, ModbusTcpParams
from modbus_connection.exceptions import ModbusConnectionError
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.nilan import connection_params
from custom_components.nilan.const import DOMAIN

AUX2_PORT = (
    "esphome-hass://esphome/01M5TK3W5CNW5C5J0FTSBCYNGR"
    "?port_udev_id=usb-FTDI_FT232R_USB_UART_BG02XIYU-if00-port0"
)
ENTRY_DATA = {
    "name": "Nilan",
    "com_type": "serial_proxy",
    "host_ip": None,
    "host_port": AUX2_PORT,
    "unit_id": 30,
    "board_type": "CTS602",
}


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, version=3, title="Nilan")
    entry.add_to_hass(hass)
    return entry


def test_connection_params_keep_the_entry_format() -> None:
    """Entries of the former integration map onto the shared connection."""
    serial = connection_params(ENTRY_DATA)
    assert serial == ModbusSerialParams(
        device=AUX2_PORT, baudrate=19200, bytesize=8, parity="E", stopbits=1
    )
    tcp = connection_params(
        {**ENTRY_DATA, "com_type": "tcp", "host_ip": "192.168.178.21", "host_port": "502"}
    )
    assert tcp == ModbusTcpParams(host="192.168.178.21", port=502)


async def test_setup_creates_the_entities_with_their_unique_ids(
    hass: HomeAssistant, mock_unit
) -> None:
    """Same unique_ids as the former integration, so entity_ids stay."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    _, params, unit_id = mock_unit.get_unit.call_args.args[1:]
    assert params.device == AUX2_PORT
    assert unit_id == 30

    entries = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    unique_ids = {e.unique_id for e in entries}
    per_domain = {}
    for e in entries:
        per_domain[e.domain] = per_domain.get(e.domain, 0) + 1
    # Live registry of the former integration (2026-11-19): 116 entries, of which 17
    # are stale_restored, i.e. not created by the former integration either
    # (heating relays 1-3, circulation pump, central heating numbers/selects/sensors,
    # T8, T13, T14). The integration itself creates 99.
    expected = {
        "binary_sensor": 8,
        "button": 1,
        "climate": 1,
        "number": 36,
        "select": 21,
        "sensor": 29,
        "switch": 1,
        "water_heater": 2,
    }
    assert per_domain == expected, repr(sorted(per_domain.items()))
    for unique_id in (
        "supply_air_temperature_t2",
        "fresh_air_intake_temperature_t1",
        "hvac",
        "top_water_heater",
        "bottom_water_heater",
        "sync_time",
    ):
        assert unique_id in unique_ids

    t2 = next(e.entity_id for e in entries if e.unique_id == "supply_air_temperature_t2")
    assert hass.states.get(t2).state == "18.85"


async def test_link_loss_makes_entities_unavailable_and_recovers(
    hass: HomeAssistant, mock_unit
) -> None:
    """ModbusConnectionError -> unavailable; the next good refresh brings it back."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    t2 = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id == "supply_air_temperature_t2"
    )

    mock_unit.fail_requests(ModbusConnectionError("link gone"))
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get(t2).state == STATE_UNAVAILABLE

    mock_unit.fail_requests(None)
    mock_unit.input[202] = 1900
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=62))
    await hass.async_block_till_done()
    assert hass.states.get(t2).state == "19.0"


async def test_setup_retries_when_the_device_does_not_answer(
    hass: HomeAssistant, mock_unit
) -> None:
    """No answer during setup -> ConfigEntryNotReady, Home Assistant retries."""
    mock_unit.fail_requests(ModbusConnectionError("no answer"))
    entry = _entry(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_write_triggers_a_refresh(hass: HomeAssistant, mock_unit) -> None:
    """Setting a value writes the register and reads the device back."""
    entry = _entry(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    top = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(registry, entry.entry_id)
        if e.unique_id == "top_water_heater"
    )
    await hass.services.async_call(
        "water_heater",
        "set_temperature",
        {"entity_id": top, "temperature": 50},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert mock_unit.holding[1700] == 5000
    assert hass.states.get(top).attributes["temperature"] == 50
