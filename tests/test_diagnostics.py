"""Tests for integration diagnostics."""

import json

import pytest

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_next.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_diagnostics_include_runtime_registry_state(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Diagnostics distinguish discovered streams from registered entities."""
    diagnostics = await async_get_config_entry_diagnostics(hass, init_integration)

    assert len(diagnostics["runtime"]["cameras"]) == 4
    assert diagnostics["runtime"]["camera_entities"]
    assert diagnostics["runtime"]["devices"]
    assert "ISAPI" in diagnostics

    # The diagnostics endpoint must remain JSON serializable after adding
    # registry identifiers, which Home Assistant stores as sets.
    json.dumps(diagnostics)
