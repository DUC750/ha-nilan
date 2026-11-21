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


class LosesAnswers:
    """Wrap a mock unit so the next reads fail on the link, then answer again.

    `lose` makes that many reads time out; `failures` lists the errors of the next
    reads one by one (a lost answer, then a port that cannot be opened, ...).
    """

    def __init__(self, unit, lose: int = 0) -> None:
        self._unit = unit
        self.failures: list[Exception] = []
        self.lose = lose
        self.disconnects = 0

    @property
    def lose(self) -> int:
        return len(self.failures)

    @lose.setter
    def lose(self, count: int) -> None:
        self.failures = [ModbusTimeoutError() for _ in range(count)]

    def __getattr__(self, name):
        return getattr(self._unit, name)

    async def _maybe_lose(self) -> None:
        if self.failures:
            raise self.failures.pop(0)

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        await self._maybe_lose()
        return await self._unit.read_holding_registers(address, count)

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        await self._maybe_lose()
        return await self._unit.read_input_registers(address, count)

    async def disconnect(self) -> None:
        self.disconnects += 1
        await self._unit.disconnect()


class FakeTime:
    """A clock that only moves when the cache sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_cache(unit, time: FakeTime | None = None) -> RegisterCache:
    """A cache whose pauses after link failures take no real time."""
    time = time or FakeTime()
    return RegisterCache(unit, sleep=time.sleep, clock=time.clock)


async def test_single_lost_answer_is_asked_again(unit, caplog) -> None:
    """One timeout drops the link and asks again; the refresh succeeds (#4623)."""
    unit.holding[109] = 1
    lossy = LosesAnswers(unit)
    time = FakeTime()
    cache = make_cache(lossy, time)
    await cache.async_pb_call(30, 109, 1, "holding")

    unit.holding[109] = 0
    lossy.lose = 1
    await cache.async_refresh()
    assert (await cache.async_pb_call(30, 109, 1, "holding")).registers == [0]
    assert cache.retries == 1
    assert lossy.disconnects == 1
    assert time.slept == [2.0]
    assert "asking again in 2 s" in caplog.text


async def test_port_that_cannot_be_opened_is_waited_out(unit) -> None:
    """The proxy's API connection drops: the answer is lost, opening the port fails
    until the connection is back, then the read succeeds (#4623, measured 21.11.)."""
    unit.input[201] = 1
    lossy = LosesAnswers(unit)
    time = FakeTime()
    cache = make_cache(lossy, time)
    await cache.async_pb_call(30, 201, 1, "input")

    unit.input[201] = 7
    lossy.failures = [
        ModbusTimeoutError("read_input_registers(201, 1): Response timeout"),
        ModbusConnectionError("could not open serial port esphome-hass://esphome/x"),
        ModbusConnectionError("could not open serial port esphome-hass://esphome/x"),
    ]
    await cache.async_refresh()
    assert (await cache.async_pb_call(30, 201, 1, "input")).registers == [7]
    assert time.slept == [2.0, 4.0, 8.0]
    assert cache.retries == 3
    assert lossy.disconnects == 3


async def test_link_down_beyond_the_budget_is_raised(unit) -> None:
    """When the link stays down, no attempt starts after the budget: raise the last
    error, so the coordinator marks the update failed."""
    unit.input[201] = 1
    lossy = LosesAnswers(unit)
    time = FakeTime()
    cache = make_cache(lossy, time)
    await cache.async_pb_call(30, 201, 1, "input")

    lossy.failures = [ModbusTimeoutError()] + [
        ModbusConnectionError("could not open serial port") for _ in range(10)
    ]
    with pytest.raises(ModbusConnectionError):
        await cache.async_refresh()
    # Every pause ends within 30 s of the first failure (2 + 4 + 8 + 16 = 30).
    assert time.slept == [2.0, 4.0, 8.0, 16.0]
    assert cache.retries == 4


async def test_budget_stops_attempts_that_would_start_too_late(unit) -> None:
    """Attempts that each time out use up the budget sooner (10 s per timeout)."""
    unit.input[201] = 1
    lossy = LosesAnswers(unit)
    time = FakeTime()
    cache = make_cache(lossy, time)
    await cache.async_pb_call(30, 201, 1, "input")

    original = lossy._maybe_lose

    async def slow_timeout() -> None:
        if lossy.failures:
            time.now += 10.0
        await original()

    lossy._maybe_lose = slow_timeout
    lossy.lose = 10
    with pytest.raises(ModbusTimeoutError):
        await cache.async_refresh()
    # First failure at t=10 -> deadline 40; 12+10=22, 26+10=36, then 36+8 > 40.
    assert time.slept == [2.0, 4.0]


async def test_writes_are_not_repeated(unit) -> None:
    """Only reads are asked again; a write that times out is raised at once."""
    cache = RegisterCache(unit)
    unit.fail_requests(ModbusTimeoutError())
    with pytest.raises(ModbusTimeoutError):
        await cache.async_pb_call(30, 1100, [1], "write_registers")
    assert cache.retries == 0


@pytest.mark.parametrize("error", [ModbusConnectionError("gone"), ModbusTimeoutError()])
async def test_link_errors_are_raised(unit, error) -> None:
    """A dead link raises, so the coordinator can mark the update failed."""
    unit.input[201] = 1
    cache = make_cache(unit)
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
