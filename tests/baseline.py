"""Raw registers of the VP 18comp baseline (2026-11-14, sw 3.6.64, hw 96, bus 23).

Addresses as in `registers.py`; values encoded the way the CTS602 delivers them
(temperatures in 1/100 °C, version strings as two ASCII characters per register).
"""

from __future__ import annotations


def _ascii(text: str) -> int:
    """Two characters, high byte first, as the CTS602 stores version strings."""
    return (ord(text[0]) << 8) | ord(text[1])


BASELINE_INPUT = {
    0: 23,  # bus_version
    1: _ascii(" 3"),  # app_version_major
    2: _ascii(" 6"),  # app_version_minor
    3: _ascii("64"),  # app_version_release
    5: 96,  # info_hw_type
    107: 0,  # input_p_hi
    108: 0,  # input_p_lo
    201: 1220,  # input_t1_intake: 12.2 °C
    202: 1885,  # input_t2_inlet: 18.85 °C
    205: 1778,  # input_t5_cond: 17.78 °C
    206: 1201,  # input_t6_evap: 12.01 °C
    210: 2284,  # input_t10_extern: 22.84 °C
    400: 0,  # alarm_status: no alarm
    1002: 4,  # control_state_display
    3003: 0,  # air_qual_co2_enable: no CO2 sensor
}
BASELINE_HOLDING = {
    300: [0, 30, 12, 14, 11, 2026],  # time_second .. time_year
    1000: 11,  # control_type: VP 18comp
    1700: 5500,  # hot_water_temp_set_t11: 55 °C
    1701: 4500,  # hot_water_temp_set_t12: 45 °C
}
