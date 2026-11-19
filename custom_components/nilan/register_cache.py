"""Register cache that reads the Nilan registers in blocks.

`Device` decodes every value through `async_pb_call(unit, address, count, use_call)`,
the call surface of Home Assistant's former `ModbusHub`. This cache keeps that
surface, so the decoding in `device.py` stays exactly as it was, and changes only
where the registers come from:

* `async_refresh()` reads every register a getter has asked for, grouped into
  blocks per register space, and replaces the snapshot.
* A read served from the snapshot does no I/O.
* A read of a register that is not in the snapshot yet goes to the device once and
  joins the plan, so the next refresh carries it in a block. This is how the plan
  is learned: no second register list has to be kept next to the getters.
* A block the device refuses with a Modbus exception response (an address it does
  not know inside the block) is read register by register from then on, and a
  register the device refuses on its own reads as `None`, exactly as a failed read
  did before.
* Link failures (`ModbusConnectionError`, `ModbusTimeoutError`, `ModbusProtocolError`)
  are raised, so the coordinator marks the update as failed and the entities go
  unavailable. There is no reconnect logic here: the shared connection reconnects
  on the next request.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
import logging

from modbus_connection import ModbusUnit
from modbus_connection.exceptions import ModbusExceptionError

_LOGGER = logging.getLogger(__name__)

HOLDING = "holding"
INPUT = "input"
WRITE_REGISTERS = "write_registers"

MAX_BLOCK = 32
"""Most registers read in one request. Modbus allows 125; the CTS602 is a small
controller, so blocks stay short to keep each request well inside its buffers."""

MAX_GAP = 4
"""Unwanted registers a block may span to join two wanted ones."""


@dataclass(frozen=True)
class RegisterResult:
    """Read result with the `.registers` attribute the getters expect."""

    registers: list[int]


@dataclass(frozen=True)
class Block:
    """One planned read: `count` registers from `address` in `space`."""

    space: str
    address: int
    count: int


def plan_blocks(
    space: str,
    addresses: Iterable[int],
    max_block: int = MAX_BLOCK,
    max_gap: int = MAX_GAP,
) -> list[Block]:
    """Group addresses into blocks no longer than `max_block`.

    Two wanted addresses share a block when at most `max_gap` unwanted registers
    lie between them and the block stays within `max_block` registers.
    """
    blocks: list[Block] = []
    start: int | None = None
    end = 0
    for address in sorted(set(addresses)):
        if start is not None and address - end - 1 <= max_gap and (
            address - start + 1 <= max_block
        ):
            end = address
            continue
        if start is not None:
            blocks.append(Block(space, start, end - start + 1))
        start = end = address
    if start is not None:
        blocks.append(Block(space, start, end - start + 1))
    return blocks


class RegisterCache:
    """Serve `async_pb_call` reads from block-read snapshots of one unit."""

    def __init__(
        self,
        unit: ModbusUnit,
        *,
        max_block: int = MAX_BLOCK,
        max_gap: int = MAX_GAP,
    ) -> None:
        """Create a cache for `unit`."""
        self._unit = unit
        self._max_block = max_block
        self._max_gap = max_gap
        self._wanted: dict[str, set[int]] = {HOLDING: set(), INPUT: set()}
        self._values: dict[tuple[str, int], int | None] = {}
        self._split: set[Block] = set()
        self._blocks: dict[str, list[Block]] | None = None
        self.on_write: Callable[[], Awaitable[None]] | None = None
        self.requests = 0
        """Read requests sent to the device since start (for diagnostics and tests)."""

    @property
    def wanted(self) -> dict[str, frozenset[int]]:
        """The registers the plan covers, per space."""
        return {space: frozenset(addrs) for space, addrs in self._wanted.items()}

    @property
    def blocks(self) -> list[Block]:
        """The planned block reads."""
        return [block for blocks in self._plan().values() for block in blocks]

    def _plan(self) -> dict[str, list[Block]]:
        if self._blocks is None:
            self._blocks = {
                space: plan_blocks(space, addrs, self._max_block, self._max_gap)
                for space, addrs in self._wanted.items()
            }
        return self._blocks

    async def _read(self, space: str, address: int, count: int) -> list[int]:
        self.requests += 1
        if space == HOLDING:
            return await self._unit.read_holding_registers(address, count)
        return await self._unit.read_input_registers(address, count)

    async def _read_single(
        self, values: dict[tuple[str, int], int | None], space: str, address: int
    ) -> None:
        try:
            values[(space, address)] = (await self._read(space, address, 1))[0]
        except ModbusExceptionError as err:
            _LOGGER.debug("%s register %s refused: %s", space, address, err)
            values[(space, address)] = None

    async def async_refresh(self) -> None:
        """Read every planned register and replace the snapshot.

        Raises the link errors of `modbus_connection`; a Modbus exception response
        only marks the refused registers.
        """
        values: dict[tuple[str, int], int | None] = {}
        for space, blocks in self._plan().items():
            wanted = self._wanted[space]
            for block in blocks:
                if block not in self._split:
                    try:
                        registers = await self._read(space, block.address, block.count)
                    except ModbusExceptionError as err:
                        _LOGGER.debug(
                            "%s block %s+%s refused (%s), reading it per register",
                            space,
                            block.address,
                            block.count,
                            err,
                        )
                        self._split.add(block)
                    else:
                        for offset, value in enumerate(registers[: block.count]):
                            values[(space, block.address + offset)] = value
                        continue
                for address in range(block.address, block.address + block.count):
                    if address in wanted:
                        await self._read_single(values, space, address)
        self._values = values

    async def _read_through(self, space: str, address: int, count: int) -> None:
        """Read registers that are not in the snapshot and add them to the plan."""
        try:
            registers = await self._read(space, address, count)
        except ModbusExceptionError:
            if count == 1:
                self._values[(space, address)] = None
            else:
                for offset in range(count):
                    await self._read_single(self._values, space, address + offset)
        else:
            for offset, value in enumerate(registers[:count]):
                self._values[(space, address + offset)] = value
        self._wanted[space].update(range(address, address + count))
        self._blocks = None

    async def async_pb_call(
        self, unit_id: int, address: int, value, use_call: str
    ) -> RegisterResult | None:
        """Read from the snapshot or write through, like `ModbusHub.async_pb_call`.

        For reads `value` is the register count; for `write_registers` it is the
        list of values. Returns `None` where the device refused a register.
        """
        if use_call == WRITE_REGISTERS:
            return await self._write(address, value)
        if use_call not in (HOLDING, INPUT):
            raise ValueError(f"unsupported call {use_call!r}")
        count = int(value)
        keys = [(use_call, address + offset) for offset in range(count)]
        if any(key not in self._values for key in keys):
            await self._read_through(use_call, address, count)
        registers = [self._values.get(key) for key in keys]
        if any(register is None for register in registers):
            return None
        return RegisterResult(registers)  # type: ignore[arg-type]

    async def _write(self, address: int, values) -> RegisterResult | None:
        if isinstance(values, int):
            values = [values]
        words = [int(word) & 0xFFFF for word in values]
        try:
            await self._unit.write_registers(address, words)
        except ModbusExceptionError as err:
            _LOGGER.error("Writing register %s was refused: %s", address, err)
            return None
        for offset, word in enumerate(words):
            if (HOLDING, address + offset) in self._values:
                self._values[(HOLDING, address + offset)] = word
        if self.on_write is not None:
            await self.on_write()
        return RegisterResult(words)
