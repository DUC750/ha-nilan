# Based on veista/nilan by Taneli Veistinen (Apache License 2.0); modified in
# DUC750/ha-nilan (coordinator-based updates), see NOTICE.
"""Config flow for Nilan integration."""

from __future__ import annotations

import logging
from typing import Any, Optional

from modbus_connection.exceptions import ModbusError
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.modbus.connection import async_get_temporary_unit
from homeassistant.exceptions import HomeAssistantError

from . import connection_params
from .const import DOMAIN
from .device_map import CTS602_DEVICE_TYPES
from .registers import CTS602HoldingRegisters

try:  # HA 2026.10+: selector config optional, value is a str
    from homeassistant.helpers.selector import SerialPortSelector

    _PORT_FIELD = SerialPortSelector()
except ImportError:  # older HA: accept the port URL as plain text
    _PORT_FIELD = str

STEP_TCP_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Nilan"): str,
        vol.Required("host_ip"): str,
        vol.Required("host_port", default="502"): str,
        vol.Required("unit_id", default=30): int,
    }
)

STEP_SERIAL_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Nilan"): str,
        vol.Required("host_port"): str,
        vol.Required("unit_id", default=30): int,
    }
)

STEP_SERIAL_PROXY_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("name", default="Nilan"): str,
        vol.Required("host_port"): _PORT_FIELD,
        vol.Required("unit_id", default=30): int,
    }
)

_LOGGER = logging.getLogger(__name__)


async def async_validate_device(hass, data: dict[str, Any]) -> None:
    """Read the controller type over the connection `data` describes.

    Uses Home Assistant's shared Modbus connection, so a check against the port of a
    running entry shares that entry's link instead of opening a second one.
    """
    try:
        async with async_get_temporary_unit(
            hass, connection_params(data), int(data["unit_id"])
        ) as unit:
            registers = await unit.read_holding_registers(
                CTS602HoldingRegisters.control_type, 1
            )
    except HomeAssistantError as error:
        raise ValueError("cannot_connect") from error
    except ModbusError as error:
        _LOGGER.debug("Validation read failed: %s", error)
        raise ValueError("cannot_connect") from error
    if len(registers) == 0:
        raise ValueError("invalid_response")
    if registers[0] not in CTS602_DEVICE_TYPES:
        _LOGGER.debug("Device Type %s not found in supported devices list", registers[0])
        raise ValueError("unsupported_device")


class NilanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Nilan CTS602 Modbus TCP."""

    VERSION = 3

    data: Optional[dict(str, Any)]

    async def async_step_user(self, user_input: Optional[dict(str, Any)] = None):
        """Invoke when a user initiates a flow via the user interface."""
        return await self.async_step_menu(user_input)

    async def async_step_menu(self, user_input: Optional[dict(str, Any)] = None):
        """Show Communications Selection."""
        return self.async_show_menu(
            step_id="menu",
            menu_options=["tcp", "serial", "serial_proxy"],
            # description_placeholders={
            #     "model": "Example model",
            # },
        )

    async def async_step_tcp(self, user_input: Optional[dict(str, Any)] = None):
        """Configure ModBus TCP entry."""
        errors: dict(str, str) = {}

        if user_input is not None:
            try:
                await async_validate_device(
                    self.hass, {**user_input, "com_type": "tcp"}
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                # Input is valid, set data.
                self.data = user_input
                self.data.update({"com_type": "tcp"})
                self.data.update({"board_type": "CTS602"})
                return self.async_create_entry(title=user_input["name"], data=self.data)
        return self.async_show_form(
            step_id="tcp", data_schema=STEP_TCP_DATA_SCHEMA, errors=errors
        )

    async def async_step_serial(self, user_input: Optional[dict(str, Any)] = None):
        """Configure ModBus Serial RTU entry."""
        errors: dict(str, str) = {}

        if user_input is not None:
            try:
                await async_validate_device(
                    self.hass, {**user_input, "com_type": "serial"}
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                # Input is valid, set data.
                self.data = user_input
                self.data.update({"com_type": "serial"})
                self.data.update({"host_ip": None})
                self.data.update({"board_type": "CTS602"})
                return self.async_create_entry(title=user_input["name"], data=self.data)
        return self.async_show_form(
            step_id="serial", data_schema=STEP_SERIAL_DATA_SCHEMA, errors=errors
        )

    async def async_step_serial_proxy(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Configure Modbus RTU over a serial port selected in Home Assistant."""
        errors: dict(str, str) = {}

        if user_input is not None:
            try:
                await async_validate_device(
                    self.hass, {**user_input, "com_type": "serial_proxy"}
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                self.data = user_input
                self.data.update({"com_type": "serial_proxy"})
                self.data.update({"host_ip": None})
                self.data.update({"board_type": "CTS602"})
                return self.async_create_entry(title=user_input["name"], data=self.data)
        return self.async_show_form(
            step_id="serial_proxy",
            data_schema=STEP_SERIAL_PROXY_DATA_SCHEMA,
            errors=errors,
        )

    # --- Reconfigure: switch the transport of an existing entry in place -------
    # Entity unique_ids do not depend on the entry, but a second entry would
    # collide with them. Changing the transport on the existing entry keeps all
    # entity_ids, statistics and automations untouched. Rollback = reconfigure
    # back to TCP.

    async def async_step_reconfigure(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Choose the new transport for an existing entry."""
        return self.async_show_menu(
            step_id="reconfigure",
            menu_options=["reconfigure_tcp", "reconfigure_serial_proxy"],
        )

    async def async_step_reconfigure_tcp(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Switch an existing entry to Modbus TCP."""
        entry = self._get_reconfigure_entry()
        errors: dict(str, str) = {}
        if user_input is not None:
            try:
                await async_validate_device(
                    self.hass, {**user_input, "com_type": "tcp"}
                )
            except ValueError as error:
                errors["base"] = str(error)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        "com_type": "tcp",
                        "host_ip": user_input["host_ip"],
                        "host_port": user_input["host_port"],
                        "unit_id": user_input["unit_id"],
                    },
                )
        current = entry.data
        schema = vol.Schema(
            {
                vol.Required("host_ip", default=current.get("host_ip") or ""): str,
                vol.Required(
                    "host_port",
                    default=current.get("host_port")
                    if current.get("com_type") == "tcp"
                    else "502",
                ): str,
                vol.Required("unit_id", default=current.get("unit_id", 30)): int,
            }
        )
        return self.async_show_form(
            step_id="reconfigure_tcp", data_schema=schema, errors=errors
        )

    async def async_step_reconfigure_serial_proxy(
        self, user_input: Optional[dict(str, Any)] = None
    ):
        """Switch an existing entry to Modbus RTU over a serial proxy port."""
        entry = self._get_reconfigure_entry()
        errors: dict(str, str) = {}
        if user_input is not None:
            try:
                await async_validate_device(
                    self.hass, {**user_input, "com_type": "serial_proxy"}
                )
            except ValueError as error:
                errors["base"] = str(error)
            except Exception:  # noqa: BLE001  serialx raises its own types
                _LOGGER.exception("Serial proxy validation failed")
                errors["base"] = "cannot_connect"
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        "com_type": "serial_proxy",
                        "host_ip": None,
                        "host_port": user_input["host_port"],
                        "unit_id": user_input["unit_id"],
                    },
                )
        schema = vol.Schema(
            {
                vol.Required("host_port"): _PORT_FIELD,
                vol.Required("unit_id", default=entry.data.get("unit_id", 30)): int,
            }
        )
        return self.async_show_form(
            step_id="reconfigure_serial_proxy", data_schema=schema, errors=errors
        )
