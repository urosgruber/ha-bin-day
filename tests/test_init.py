"""Tests for the integration scaffold."""

import json
from pathlib import Path

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

MANIFEST = Path(__file__).parent.parent / "custom_components" / "bin_day" / "manifest.json"


async def test_manifest_and_setup(hass: HomeAssistant) -> None:
    """The manifest is correct and an empty entry sets up."""
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["domain"] == "bin_day"
    assert manifest["version"] == "0.1.0"
    assert manifest["config_flow"] is True
    assert "http" in manifest["dependencies"]
    assert "frontend" in manifest["dependencies"]

    entry = MockConfigEntry(domain="bin_day", data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
