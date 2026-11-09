"""Tests for the config flow."""

from datetime import date
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

import pytest

from custom_components.bin_day.const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_TYPES,
    DOMAIN,
)
from custom_components.bin_day.simbio import SimbioAddress
from custom_components.bin_day.source import SourceError

ADDRESSES = [
    SimbioAddress("100400000112129998", "LJUBLJANSKA CESTA 1 A", "CELJE"),
    SimbioAddress("100400000112130001", "LJUBLJANSKA CESTA 18 A", "CELJE"),
]
FETCHED = {
    "mko": [date(2026, 11, 12)],
    "emb": [date(2026, 11, 13)],
    "bio": [],
}
SEARCH = "custom_components.bin_day.config_flow.SimbioClient.async_search"
FETCH = "custom_components.bin_day.config_flow.SimbioClient.async_fetch_address"
SETUP = "custom_components.bin_day.async_setup_entry"


@pytest.fixture(autouse=True)
def _no_setup():
    with patch(SETUP, return_value=True):
        yield


async def _start(hass: HomeAssistant, matches=ADDRESSES):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    with patch(SEARCH, return_value=matches):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address": "Ljubljanska cesta 1 A"}
        )


async def _to_types(hass: HomeAssistant, matches=ADDRESSES, index=0):
    result = await _start(hass, matches)
    with patch(FETCH, return_value=FETCHED):
        return await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address_id": matches[index].id}
        )


async def test_full_flow(hass: HomeAssistant) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    options = result["data_schema"].schema["address_id"].config["options"]
    assert options[0] == {
        "value": ADDRESSES[0].id,
        "label": "LJUBLJANSKA CESTA 1 A, CELJE",
    }

    with patch(FETCH, return_value=FETCHED):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address_id": ADDRESSES[0].id}
        )
    assert result["step_id"] == "types"
    labels = {
        o["value"]: o["label"]
        for o in result["data_schema"].schema["types"].config["options"]
    }
    assert labels["bio"].endswith("(trenutno ni odvoza)") or labels["bio"].endswith(
        "(no collection now)"
    )
    assert "(no collection now)" not in labels["mko"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "type"
    result = await _accept_types(hass, result)
    assert result["step_id"] == "name"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Odvoz smeti"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Odvoz smeti"
    entry = result["result"]
    assert entry.unique_id == ADDRESSES[0].id
    assert entry.data[CONF_ADDRESS_ID] == ADDRESSES[0].id
    assert entry.data[CONF_ADDRESS_NAME] == "LJUBLJANSKA CESTA 1 A"
    assert entry.data[CONF_CITY] == "CELJE"
    types = entry.data[CONF_TYPES]
    assert list(types) == ["mko", "emb", "bio"]
    assert types["mko"] == {
        "name": "Mixed waste",
        "icon": "mdi:trash-can",
        "color": "#00a136",
        "estimate": True,
        "interval": None,
    }


async def test_single_match_still_shows_pick(hass: HomeAssistant) -> None:
    result = await _start(hass, ADDRESSES[:1])
    assert result["step_id"] == "pick"


async def test_no_match(hass: HomeAssistant) -> None:
    result = await _start(hass, [])
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "no_match"}


async def test_cannot_connect_on_search(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(SEARCH, side_effect=SourceError("down")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address": "x"}
        )
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_cannot_connect_on_pick(hass: HomeAssistant) -> None:
    result = await _start(hass)
    with patch(FETCH, side_effect=SourceError("down")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address_id": ADDRESSES[0].id}
        )
    assert result["step_id"] == "pick"
    assert result["errors"] == {"base": "cannot_connect"}


async def _accept_types(hass: HomeAssistant, result, overrides=None):
    """Walk through all type steps with defaults; return the name step."""
    overrides = overrides or {}
    while result["step_id"] == "type":
        key = result["description_placeholders"]["type"]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], overrides.get(key, {})
        )
    return result


async def test_invalid_color(hass: HomeAssistant) -> None:
    result = await _to_types(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "type"
    assert result["description_placeholders"] == {"type": "Mixed waste"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Moji smeti", "color": "green"}
    )
    assert result["step_id"] == "type"
    assert result["errors"] == {"color": "invalid_color"}
    # The other input stays in the form.
    values = {str(k): k.description["suggested_value"] for k in result["data_schema"].schema
              if k.description}
    assert values["name"] == "Moji smeti"
    assert values["color"] == "green"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Moji smeti", "color": "#112233"}
    )
    assert result["description_placeholders"] == {"type": "Packaging"}


async def test_empty_type_name(hass: HomeAssistant) -> None:
    result = await _to_types(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "  "}
    )
    assert result["errors"] == {"name": "invalid_name"}


async def test_no_type_selected(hass: HomeAssistant) -> None:
    result = await _to_types(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"types": []}
    )
    assert result["step_id"] == "types"
    assert result["errors"] == {"base": "no_types"}


async def test_only_selected_types_are_stored(hass: HomeAssistant) -> None:
    result = await _to_types(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"types": ["emb", "mko"]}
    )
    assert result["description_placeholders"] == {"type": "Mixed waste"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["description_placeholders"] == {"type": "Packaging"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Plastika"}
    )
    assert result["step_id"] == "name"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["title"] == "Bin Day – LJUBLJANSKA CESTA 1 A"
    types = result["result"].data[CONF_TYPES]
    assert list(types) == ["mko", "emb"]
    assert types["emb"]["name"] == "Plastika"


async def test_unknown_type_defaults(hass: HomeAssistant) -> None:
    fetched = {"mko": [], "steklo": [date(2026, 11, 20)]}
    result = await _start(hass)
    with patch(FETCH, return_value=fetched):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address_id": ADDRESSES[0].id}
        )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _accept_types(hass, result)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    unknown = result["result"].data[CONF_TYPES]["steklo"]
    assert unknown["name"] == "steklo"
    assert unknown["icon"] == "mdi:delete"
    assert unknown["color"] == "#ffdd00"


async def test_already_configured(hass: HomeAssistant) -> None:
    result = await _to_types(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _accept_types(hass, result)
    await hass.config_entries.flow.async_configure(result["flow_id"], {})

    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"address_id": ADDRESSES[0].id}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_slovenian_defaults(hass: HomeAssistant) -> None:
    hass.config.language = "sl"
    result = await _to_types(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["description_placeholders"] == {"type": "Mešani komunalni odpadki"}
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults["name"] == "Mešani komunalni odpadki"
    assert defaults["icon"] == "mdi:trash-can"
    assert defaults["color"] == "#00a136"


async def test_no_match_keeps_address(hass: HomeAssistant) -> None:
    result = await _start(hass, [])
    values = {str(k): k.description["suggested_value"] for k in result["data_schema"].schema
              if k.description}
    assert values == {"address": "Ljubljanska cesta 1 A"}
