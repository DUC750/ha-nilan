"""Decoding through the register cache, checked against a measured baseline.

Reference values: baseline of the VP 18comp taken on 2026-11-14 with the former
integration (sw 3.6.64, hw 96, ModBus version 23), e.g. T1 12.2 °C, T2 18.85 °C,
T10 22.84 °C, T5 17.78 °C, T6 12.01 °C, control state 4, no active alarm, P_HI and
P_LO off, hot water set points 55 °C (top) and 45 °C (bottom).
"""

from __future__ import annotations

from modbus_connection.mock import MockModbusConnection
import pytest

from nilan.device import Device
from nilan.register_cache import RegisterCache
from nilan.registers import CTS602HoldingRegisters as H, CTS602InputRegisters as I
from tests.baseline import BASELINE_HOLDING, BASELINE_INPUT


@pytest.fixture
async def device():
    """A VP 18comp set up through the cache, as in the baseline."""
    unit = MockModbusConnection().for_unit(30)
    unit.input.update(BASELINE_INPUT)
    unit.holding.update(BASELINE_HOLDING)
    cache = RegisterCache(unit)
    nilan = Device(None, "Nilan", cache, 30)
    await nilan.setup()
    nilan.unit = unit
    nilan.cache = cache
    return nilan


def test_baseline_addresses_match_the_register_map() -> None:
    """The raw baseline uses the addresses of registers.py."""
    assert BASELINE_INPUT[I.input_t2_inlet] == 1885
    assert BASELINE_INPUT[I.control_state_display] == 4
    assert BASELINE_INPUT[I.info_hw_type] == 96
    assert BASELINE_HOLDING[H.control_type] == 11
    assert BASELINE_HOLDING[H.hot_water_temp_set_t11] == 5500


async def test_identity_matches_the_baseline(device) -> None:
    """Model, software and hardware version as the device registry shows them."""
    assert device.get_device_type == "VP 18comp"
    assert device.get_device_sw_version == "3.6.64"
    assert device.get_device_hw_version == "96"


async def test_reference_values_decode_as_in_the_baseline(device) -> None:
    """The decoding in device.py is unchanged; only the transport moved."""
    await device.cache.async_refresh()
    assert await device.get_t1_intake_temperature() == 12.2
    assert await device.get_t2_inlet_temperature() == 18.85
    assert await device.get_t10_external_temperature() == 22.84
    assert await device.get_t5_condenser_temperature() == 17.78
    assert await device.get_t6_evaporator_temperature() == 12.01
    assert await device.get_control_state() == 4
    assert await device.get_alarm_count() == 0
    assert await device.get_high_pressure_switch_state() is False
    assert await device.get_low_pressure_switch_state() is False
    assert await device.get_electric_water_heater_setpoint() == 55
    assert await device.get_compressor_water_heater_setpoint() == 45


async def test_all_getters_cost_few_requests_after_the_first_round(device) -> None:
    """Once learned, a full round of every assigned getter costs a few block reads."""
    getters = [name for name in device.get_attributes if name.startswith("get_")]
    assert len(getters) > 100
    for name in getters:
        await getattr(device, name)()
    planned = len(device.cache.blocks)

    device.unit.read_events.clear()
    await device.cache.async_refresh()
    requests_per_refresh = len(device.unit.read_events)
    before = device.cache.requests
    for name in getters:
        await getattr(device, name)()
    assert device.cache.requests == before, "decoding after a refresh needs no I/O"
    assert requests_per_refresh == planned
    assert requests_per_refresh < len(getters) / 4
