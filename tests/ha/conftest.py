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
