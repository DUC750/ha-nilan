"""Coordinator: one block refresh per interval for all Nilan entities."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging

from modbus_connection.exceptions import ModbusError

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .register_cache import RegisterCache

_LOGGER = logging.getLogger(__name__)

UPDATE_INTERVAL = timedelta(seconds=30)
"""Same as the polling interval of the entities in the former integration."""

type Reader = Callable[[], Awaitable[None]]


class NilanCoordinator(DataUpdateCoordinator[None]):
    """Refresh the register cache, then let every entity decode its state from it.

    The entities keep their decoding in `async_read_state`; the coordinator calls it
    after each refresh, so all values of one update come from the same snapshot.
    Availability follows `last_update_success`.
    """

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, cache: RegisterCache
    ) -> None:
        """Create the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.cache = cache
        self._readers: list[Reader] = []
        cache.on_write = self.async_request_refresh

    @callback
    def async_add_reader(self, reader: Reader) -> CALLBACK_TYPE:
        """Register an entity's decode step; returns the removal callback."""
        self._readers.append(reader)

        @callback
        def remove() -> None:
            if reader in self._readers:
                self._readers.remove(reader)

        return remove

    async def _async_update_data(self) -> None:
        """Read the planned blocks, then decode every entity from the snapshot."""
        try:
            await self.cache.async_refresh()
            for reader in list(self._readers):
                try:
                    await reader()
                except ModbusError:
                    raise
                except Exception:  # noqa: BLE001 - one entity must not stop the rest
                    _LOGGER.exception("Decoding %s failed", reader)
        except ModbusError as err:
            raise UpdateFailed(f"Nilan did not answer: {err}") from err
