"""Tests for the block-reading register cache."""

from __future__ import annotations

from modbus_connection.exceptions import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTimeoutError,
)
from modbus_connection.mock import MockModbusConnection
import pytest

from nilan.register_cache import Block, RegisterCache, plan_blocks


@pytest.fixture
def unit():
    """A mock unit 30 on an in-memory connection."""
    return MockModbusConnection().for_unit(30)


def test_plan_blocks_merges_small_gaps_and_splits_long_runs() -> None:
    """Addresses close together share a block, far ones and long runs do not."""
    assert plan_blocks("input", [201, 202, 205, 220]) == [
        Block("input", 201, 5),
        Block("input", 220, 1),
    ]
    assert plan_blocks("input", range(100, 140), max_block=32) == [
        Block("input", 100, 32),
        Block("input", 132, 8),
    ]
    assert plan_blocks("holding", []) == []


async def test_first_read_goes_to_device_then_joins_the_plan(unit) -> None:
    """A miss reads through once; the next refresh carries it in a block."""
    unit.input.update({201: 1220, 202: 1885})
    cache = RegisterCache(unit)

    first = await cache.async_pb_call(30, 201, 1, "input")
    second = await cache.async_pb_call(30, 202, 1, "input")
    assert first.registers == [1220]
    assert second.registers == [1885]
    assert cache.requests == 2
    assert cache.blocks == [Block("input", 201, 2)]

    unit.input.update({201: 1300, 202: 1900})
    unit.read_events.clear()
    await cache.async_refresh()
    assert [(e.register_type, e.address, e.count) for e in unit.read_events] == [
        ("input", 201, 2)
    ]
    # Reads after a refresh come from the snapshot, without I/O.
    requests = cache.requests
    assert (await cache.async_pb_call(30, 201, 1, "input")).registers == [1300]
    assert (await cache.async_pb_call(30, 202, 1, "input")).registers == [1900]
    assert cache.requests == requests


async def test_refused_block_is_read_per_register(unit) -> None:
    """A block containing an unknown address falls back to single reads, once."""
    unit.input.update({201: 1, 203: 3})
    cache = RegisterCache(unit)
    await cache.async_pb_call(30, 201, 1, "input")
    await cache.async_pb_call(30, 203, 1, "input")
    assert cache.blocks == [Block("input", 201, 3)]

    unit.fail_read(202, IllegalDataAddressError(), register_type="input")
    await cache.async_refresh()
    assert (await cache.async_pb_call(30, 201, 1, "input")).registers == [1]
    assert (await cache.async_pb_call(30, 203, 1, "input")).registers == [3]

    # The block is remembered as split: no second refused block request.
    unit.read_events.clear()
    await cache.async_refresh()
    assert [(e.address, e.count) for e in unit.read_events] == [(201, 1), (203, 1)]


async def test_refused_register_reads_as_none(unit) -> None:
    """A register the device refuses on its own returns None, as before."""
    unit.fail_read(3003, IllegalDataAddressError(), register_type="input")
    cache = RegisterCache(unit)
    assert await cache.async_pb_call(30, 3003, 1, "input") is None
    await cache.async_refresh()
    assert await cache.async_pb_call(30, 3003, 1, "input") is None


@pytest.mark.parametrize("error", [ModbusConnectionError("gone"), ModbusTimeoutError()])
async def test_link_errors_are_raised(unit, error) -> None:
    """A dead link raises, so the coordinator can mark the update failed."""
    unit.input[201] = 1
    cache = RegisterCache(unit)
    await cache.async_pb_call(30, 201, 1, "input")
    unit.fail_requests(error)
    with pytest.raises(type(error)):
        await cache.async_refresh()
    unit.fail_requests(None)
    await cache.async_refresh()
    assert (await cache.async_pb_call(30, 201, 1, "input")).registers == [1]


async def test_write_goes_to_device_updates_snapshot_and_notifies(unit) -> None:
    """Writes pass through, negative values as two's complement, then refresh."""
    unit.holding[1700] = 50
    cache = RegisterCache(unit)
    notified = []

    async def on_write() -> None:
        notified.append(True)

    cache.on_write = on_write
    await cache.async_pb_call(30, 1700, 1, "holding")
    result = await cache.async_pb_call(30, 1700, [55], "write_registers")
    assert result.registers == [55]
    assert unit.holding[1700] == 55
    assert (await cache.async_pb_call(30, 1700, 1, "holding")).registers == [55]
    assert notified == [True]

    await cache.async_pb_call(30, 1701, [-1], "write_registers")
    assert unit.holding[1701] == 0xFFFF


async def test_refused_write_returns_none(unit) -> None:
    """A write the device refuses returns None, like the former hub did."""
    unit.fail_write(1700, IllegalDataAddressError())
    cache = RegisterCache(unit)
    assert await cache.async_pb_call(30, 1700, [1], "write_registers") is None
