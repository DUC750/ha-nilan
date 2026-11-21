"""Fixtures for tests that run the integration inside Home Assistant."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

from modbus_connection.mock import MockModbusConnection, MockModbusUnit
import pytest

from tests.baseline import BASELINE_HOLDING, BASELINE_INPUT


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations) -> Iterator[None]:
    """Load custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def no_retry_pauses() -> Iterator[None]:
    """Raise link failures at once instead of waiting up to 30 s of real time.

    The waiting itself is covered by tests/unit/test_register_cache.py; a test here
    that needs it patches `RETRY_DELAYS` itself.
    """
    with patch("custom_components.nilan.register_cache.RETRY_DELAYS", ()):
        yield


@pytest.fixture
def mock_unit() -> Iterator[MockModbusUnit]:
    """A VP 18comp with the baseline registers behind Home Assistant's connection."""
    unit = MockModbusConnection().for_unit(30)
    unit.input.update(BASELINE_INPUT)
    unit.holding.update(BASELINE_HOLDING)
    with patch(
        "custom_components.nilan.async_get_unit", return_value=unit
    ) as get_unit:
        unit.get_unit = get_unit
        yield unit
