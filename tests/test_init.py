"""Tests for the integration setup."""

from datetime import date
import json
from pathlib import Path
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bin_day import _create_source
from custom_components.bin_day.const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_TYPES,
)
from custom_components.bin_day.simbio import SimbioSource

MANIFEST = Path(__file__).parent.parent / "custom_components" / "bin_day" / "manifest.json"

DATA = {
    CONF_ADDRESS_ID: "1",
    CONF_ADDRESS_NAME: "Ljubljanska cesta 1 A",
    CONF_CITY: "Ljubljana",
    CONF_TYPES: {"mko": {"name": "Mixed waste", "estimate": True, "interval": None}},
}


class _Source:
    async def async_fetch(self) -> dict[str, list[date]]:
        return {"mko": [date(2099, 1, 1)]}


async def test_manifest_and_setup(hass: HomeAssistant) -> None:
    """The manifest is correct and an entry sets up and unloads."""
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["domain"] == "bin_day"
    assert manifest["version"] == "0.1.4"
    assert manifest["config_flow"] is True
    assert "http" in manifest["dependencies"]
    assert "frontend" in manifest["dependencies"]

    entry = MockConfigEntry(domain="bin_day", data=DATA)
    entry.add_to_hass(hass)
    with patch("custom_components.bin_day._create_source", return_value=_Source()):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_create_source_uses_stored_address(hass: HomeAssistant) -> None:
    """The source is a Simbio source for the stored address."""
    entry = MockConfigEntry(domain="bin_day", data=DATA)
    source = _create_source(hass, entry)
    assert isinstance(source, SimbioSource)
    assert source._address.id == "1"
    assert source._address.name == "Ljubljanska cesta 1 A"
    assert source._address.city == "Ljubljana"


async def test_card_is_served(hass: HomeAssistant, hass_client) -> None:
    """After setup, HA serves the card file."""
    entry = MockConfigEntry(domain="bin_day", data=DATA)
    entry.add_to_hass(hass)
    with patch("custom_components.bin_day._create_source", return_value=_Source()):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
    client = await hass_client()
    resp = await client.get("/bin_day/bin-day-card.js")
    assert resp.status == 200
    body = await resp.text()
    assert 'customElements.define("bin-day-card"' in body


async def test_remove_entry_deletes_issue_and_store(hass: HomeAssistant, hass_storage) -> None:
    """Removing the entry deletes its repair issue and its store."""
    entry = MockConfigEntry(domain="bin_day", data=DATA)
    entry.add_to_hass(hass)
    with patch("custom_components.bin_day._create_source", return_value=_Source()):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
    store_key = f"bin_day.{entry.entry_id}"
    assert store_key in hass_storage
    issue_id = f"address_not_found_{entry.entry_id}"
    ir.async_create_issue(
        hass,
        "bin_day",
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="address_not_found",
        translation_placeholders={"address": "x"},
    )

    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue("bin_day", issue_id) is None
    assert store_key not in hass_storage
