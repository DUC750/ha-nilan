# Nilan

Home Assistant integration for Nilan ventilation and heat pump units with a **CTS602**
controller (Modbus), domain `nilan`.

It is based on [veista/nilan](https://github.com/veista/nilan) by **Taneli Veistinen**
(Apache License 2.0, see [NOTICE](NOTICE)): the register map, the device selection and the
decoding of every value are his work. This repository replaces the transport and the polling
underneath.

## What changed compared to veista/nilan

| Area | Change |
| --- | --- |
| Transport | Home Assistant's shared Modbus connection (`homeassistant.components.modbus.connection`, library `modbus-connection`) for Modbus TCP and for serial ports, including serial ports Home Assistant hands out as URLs (for example an ESPHome serial proxy such as the Home Assistant Connect AUX-2). No own Modbus client, no own reconnect logic. |
| Polling | One `DataUpdateCoordinator` every 30 s. It reads the registers in blocks (holding and input separately) instead of one request per entity. Entities are unavailable while the device does not answer (`last_update_success`). |
| Block plan | Learned from the decoding itself: the first round reads each register once, later rounds read them in blocks. A block the controller refuses is read register by register from then on. |
| Writes | Register by register, as before, followed by a refresh. |
| Device type 11 (VP 18comp) | Entity mapping, pressure switch inputs P_HI / P_LO and `hw_version` as string, carried over from the fork DUC750/nilan. |

Unchanged on purpose: domain `nilan`, config entry format (version 3) and every entity
`unique_id`. An existing entry of veista/nilan or DUC750/nilan keeps its entities, entity IDs,
history and statistics when this integration replaces the old files.

## Requirements

- Home Assistant **2026.11.0** or newer (shared Modbus connection).
- A Modbus link to the CTS602: a Modbus TCP gateway, a USB–RS485 adapter, or a serial port
  selected in Home Assistant. Serial line: 19200 baud, 8E1. Default unit ID 30.

## Installation

### HACS

Add this repository as a custom repository (type *Integration*), install *Nilan*, restart
Home Assistant, then add *Nilan* under *Settings → Devices & services*.

### Replacing veista/nilan or DUC750/nilan

Both install into `custom_components/nilan`. Removing one HACS repository deletes that
directory even when another repository installed it, so:

1. Copy `custom_components/nilan` aside.
2. Install this repository in HACS, then remove the old one, then check that
   `custom_components/nilan` still holds this integration (reinstall if not).
3. Restart Home Assistant. Do **not** add a second entry; the existing one is used.

### Switching the transport of an entry

*Nilan → ⋮ → Reconfigure* → Modbus TCP or serial port. The entry and all entities stay.

## Supported devices

CTS602 device types as listed in `device_map.py` (Comfort, VP 18 series, Compact, COMBI,
VENTEC, CompactP AIR/GEO and others). Tested on a VP 18comp (software 3.6.64, ModBus version 23).

## Development

```bash
python -m pip install -r requirements_test.txt
python -m pytest            # all tests (needs Home Assistant, Linux)
python -m pytest tests/unit # cache and decoding only (no Home Assistant needed)
```

## License

Apache License 2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE).
