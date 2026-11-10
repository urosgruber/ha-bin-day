"""Tests for the config flow."""

from datetime import date
from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bin_day.config_flow import notify_targets, phone_targets
from custom_components.bin_day.const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_NOTIFY_TARGETS,
    CONF_REMINDERS_ENABLED,
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



PHONES = [("iOS", "Phone A"), ("android", "Phone B"), ("Windows", "Laptop C")]
OLD_DEFAULTS = {
    "mko": {
        "name": "Mixed waste",
        "icon": "mdi:trash-can",
        "color": "#00a136",
        "estimate": True,
        "interval": None,
    },
    "emb": {
        "name": "Packaging",
        "icon": "mdi:recycle",
        "color": "#ffdd00",
        "estimate": True,
        "interval": None,
    },
    "bio": {
        "name": "Bio waste",
        "icon": "mdi:leaf",
        "color": "#8d6e63",
        "estimate": True,
        "interval": None,
    },
}


async def _noop(call: ServiceCall) -> None:
    return None


def _add_phones(
    hass: HomeAssistant,
    devices=PHONES,
    with_service=True,
    linked=True,
    names: dict[str, str] | None = None,
) -> None:
    """Fake mobile_app entries and, optionally, their notify actions.

    Each device gets a device registry entry and a device tracker. When
    `linked` is true, a person lists that tracker, so the device is a phone
    of a person. `names` maps a device name to its name_by_user.
    """
    for os_name, device_name in devices:
        slug = slugify(device_name)
        entry = MockConfigEntry(
            domain="mobile_app",
            data={"os_name": os_name, "device_name": device_name},
        )
        entry.add_to_hass(hass)
        device = dr.async_get(hass).async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("mobile_app", slug)},
            name=device_name,
        )
        if names and device_name in names:
            dr.async_get(hass).async_update_device(
                device.id, name_by_user=names[device_name]
            )
        tracker = er.async_get(hass).async_get_or_create(
            "device_tracker",
            "mobile_app",
            slug,
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=slug,
        )
        if linked:
            hass.states.async_set(
                f"person.{slug}", "home", {"device_trackers": [tracker.entity_id]}
            )
        if with_service:
            hass.services.async_register("notify", f"mobile_app_{slug}", _noop)


def _defaults(result) -> dict:
    return {
        str(k): k.default()
        for k in result["data_schema"].schema
        if callable(k.default)
    }


async def _to_final(hass: HomeAssistant, matches=ADDRESSES, fetched=FETCHED):
    """Go through the address steps and return the final form."""
    result = await _start(hass, matches)
    if result["step_id"] == "pick":
        with patch(FETCH, return_value=fetched):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"address_id": matches[0].id}
            )
    return result


async def test_several_matches_show_pick(hass: HomeAssistant) -> None:
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "pick"
    options = result["data_schema"].schema["address_id"].config["options"]
    assert options[0] == {
        "value": ADDRESSES[0].id,
        "label": "LJUBLJANSKA CESTA 1 A, CELJE",
    }


async def test_one_match_skips_pick(hass: HomeAssistant) -> None:
    with patch(FETCH, return_value=FETCHED):
        result = await _start(hass, ADDRESSES[:1])
    assert result["step_id"] == "name"
    assert result["last_step"] is not False


async def test_one_match_cannot_connect_shows_user_again(hass: HomeAssistant) -> None:
    with patch(FETCH, side_effect=SourceError("down")):
        result = await _start(hass, ADDRESSES[:1])
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}
    values = {
        str(k): k.description["suggested_value"]
        for k in result["data_schema"].schema
        if k.description
    }
    assert values == {"address": "Ljubljanska cesta 1 A"}


async def test_one_match_already_configured_aborts(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=ADDRESSES[0].id).add_to_hass(hass)
    with patch(FETCH, return_value=FETCHED):
        result = await _start(hass, ADDRESSES[:1])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_final_step_defaults(hass: HomeAssistant) -> None:
    _add_phones(hass)
    # A phone without a notify action and a notify action without a phone.
    _add_phones(hass, [("iPadOS", "Tablet D")], with_service=False)
    hass.services.async_register("notify", "send_message", _noop)
    hass.services.async_register("notify", "mobile_app_laptop_c", _noop)
    result = await _to_final(hass)
    assert result["step_id"] == "name"
    assert result["last_step"] is not False
    schema = result["data_schema"].schema
    assert _defaults(result) == {
        "types": ["mko", "emb", "bio"],
        "name": "Bin Day – LJUBLJANSKA CESTA 1 A",
        CONF_NOTIFY_TARGETS: ["mobile_app_phone_a", "mobile_app_phone_b"],
    }
    labels = {o["value"]: o["label"] for o in schema["types"].config["options"]}
    assert labels["bio"] == "Bio waste (no collection now)"
    assert labels["mko"] == "Mixed waste"
    assert schema["types"].config["sort"] is False
    offered = [o["value"] for o in schema[CONF_NOTIFY_TARGETS].config["options"]]
    # The select lists the devices by their label: Laptop C, Phone A, Phone B.
    assert offered == [
        "mobile_app_laptop_c",
        "mobile_app_phone_a",
        "mobile_app_phone_b",
    ]


async def test_phone_targets_helper(hass: HomeAssistant) -> None:
    assert phone_targets(hass) == []
    _add_phones(hass, [("IOS", "Zed"), ("Android", "Amy"), ("iPadOS", "Pad")])
    _add_phones(hass, [("Linux", "Box")])
    assert phone_targets(hass) == [
        "mobile_app_amy",
        "mobile_app_pad",
        "mobile_app_zed",
    ]


async def test_phone_targets_exclude_device_without_person(hass: HomeAssistant) -> None:
    _add_phones(hass, [("Android", "Wall panel")], linked=False)
    assert phone_targets(hass) == []


async def test_phone_targets_include_linked_iphone(hass: HomeAssistant) -> None:
    _add_phones(hass, [("iOS", "Phone A")])
    _add_phones(hass, [("Android", "Wall panel")], linked=False)
    assert phone_targets(hass) == ["mobile_app_phone_a"]


async def test_phone_targets_exclude_linked_macos(hass: HomeAssistant) -> None:
    _add_phones(hass, [("macOS", "Laptop C")])
    assert phone_targets(hass) == []


async def test_phone_targets_exclude_phone_without_notify_action(
    hass: HomeAssistant,
) -> None:
    _add_phones(hass, [("iOS", "Phone A")], with_service=False)
    assert phone_targets(hass) == []


async def test_notify_targets_label_uses_user_given_device_name(
    hass: HomeAssistant,
) -> None:
    _add_phones(hass, [("iOS", "Phone A")], names={"Phone A": "Kitchen phone"})
    hass.services.async_register("notify", "family_tablet", _noop)
    options = notify_targets(hass)
    assert {o["value"]: o["label"] for o in options} == {
        "mobile_app_phone_a": "Kitchen phone",
        "family_tablet": "family_tablet",
    }
    # Sorted by label, ignoring case.
    assert [o["label"] for o in options] == ["family_tablet", "Kitchen phone"]


async def test_notify_targets_label_falls_back_to_device_name(
    hass: HomeAssistant,
) -> None:
    _add_phones(hass, [("iOS", "Phone A")])
    assert notify_targets(hass) == [
        {"value": "mobile_app_phone_a", "label": "Phone A"}
    ]


async def test_no_phones_means_reminders_off(hass: HomeAssistant) -> None:
    result = await _to_final(hass)
    assert _defaults(result)[CONF_NOTIFY_TARGETS] == []
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {}
    assert result["result"].options == {}


async def test_phones_turn_reminders_on(hass: HomeAssistant) -> None:
    _add_phones(hass)
    result = await _to_final(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["result"].options
    assert options == {
        CONF_REMINDERS_ENABLED: True,
        CONF_NOTIFY_TARGETS: ["mobile_app_phone_a", "mobile_app_phone_b"],
    }
    entry = result["result"]
    assert entry.title == "Bin Day – LJUBLJANSKA CESTA 1 A"
    assert entry.unique_id == ADDRESSES[0].id
    assert entry.data[CONF_ADDRESS_ID] == ADDRESSES[0].id
    assert entry.data[CONF_ADDRESS_NAME] == "LJUBLJANSKA CESTA 1 A"
    assert entry.data[CONF_CITY] == "CELJE"


async def test_chosen_targets_and_name(hass: HomeAssistant) -> None:
    _add_phones(hass)
    result = await _to_final(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"name": "Moji smeti", CONF_NOTIFY_TARGETS: ["mobile_app_phone_b"]},
    )
    assert result["title"] == "Moji smeti"
    assert result["result"].options == {
        CONF_REMINDERS_ENABLED: True,
        CONF_NOTIFY_TARGETS: ["mobile_app_phone_b"],
    }


async def test_empty_targets_turn_reminders_off(hass: HomeAssistant) -> None:
    _add_phones(hass)
    result = await _to_final(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_NOTIFY_TARGETS: []}
    )
    assert result["result"].options == {}


async def test_no_notify_actions_still_shows_the_field(hass: HomeAssistant) -> None:
    result = await _to_final(hass)
    field = result["data_schema"].schema[CONF_NOTIFY_TARGETS]
    assert field.config["options"] == []
    # The form must be valid with no notify action at all.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "x", CONF_NOTIFY_TARGETS: []}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_stored_types_equal_the_old_defaults(hass: HomeAssistant) -> None:
    """Accepting every default stores what the old per-type steps stored."""
    result = await _to_final(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    types = result["result"].data[CONF_TYPES]
    assert list(types) == ["mko", "emb", "bio"]
    assert types == OLD_DEFAULTS


async def test_only_selected_types_are_stored(hass: HomeAssistant) -> None:
    result = await _to_final(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"types": ["emb", "mko"]}
    )
    types = result["result"].data[CONF_TYPES]
    assert list(types) == ["mko", "emb"]
    assert types == {k: OLD_DEFAULTS[k] for k in ("mko", "emb")}


async def test_no_type_selected(hass: HomeAssistant) -> None:
    result = await _to_final(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"types": [], "name": "Moje"}
    )
    assert result["step_id"] == "name"
    assert result["errors"] == {"base": "no_types"}
    assert result["last_step"] is not False
    assert result["data_schema"].schema["types"].config["sort"] is False
    suggested = {
        str(k): k.description["suggested_value"]
        for k in result["data_schema"].schema
        if k.description
    }
    assert suggested["name"] == "Moje"


async def test_unknown_type_defaults(hass: HomeAssistant) -> None:
    fetched = {"mko": [], "steklo": [date(2026, 11, 20)]}
    result = await _to_final(hass, fetched=fetched)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    unknown = result["result"].data[CONF_TYPES]["steklo"]
    assert unknown == {
        "name": "steklo",
        "icon": "mdi:delete",
        "color": "#ffdd00",
        "estimate": True,
        "interval": None,
    }


async def test_already_configured(hass: HomeAssistant) -> None:
    result = await _to_final(hass)
    await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"address_id": ADDRESSES[0].id}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_slovenian_defaults(hass: HomeAssistant) -> None:
    hass.config.language = "sl"
    result = await _to_final(hass)
    labels = {
        o["value"]: o["label"]
        for o in result["data_schema"].schema["types"].config["options"]
    }
    assert labels["bio"] == "Biološki odpadki (trenutno ni odvoza)"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["result"].data[CONF_TYPES]["mko"]["name"] == "Mešani komunalni odpadki"


async def test_last_step_flags(hass: HomeAssistant) -> None:
    """The first two steps show Next; the final step shows Submit."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["last_step"] is False
    result = await _start(hass)
    assert result["step_id"] == "pick"
    assert result["last_step"] is False
    with patch(FETCH, return_value=FETCHED):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address_id": ADDRESSES[0].id}
        )
    assert result["step_id"] == "name"
    assert result.get("last_step") is not False


async def test_existing_entries_are_not_migrated(hass: HomeAssistant) -> None:
    """Entries with options {} or with reminders off load and stay as they are."""
    from .test_coordinator import FakeSource, make_entry, setup, unload

    for options in ({}, {CONF_REMINDERS_ENABLED: False}):
        entry = make_entry(options)
        assert await setup(hass, entry, FakeSource({"mko": [], "emb": [], "bio": []}))
        assert entry.options == options
        assert entry.version == 1
        await unload(hass, entry)
        await hass.config_entries.async_remove(entry.entry_id)


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
            result["flow_id"], {"address": "Celjska"}
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


async def test_no_match_keeps_address(hass: HomeAssistant) -> None:
    result = await _start(hass, [])
    values = {str(k): k.description["suggested_value"] for k in result["data_schema"].schema
              if k.description}
    assert values == {"address": "Ljubljanska cesta 1 A"}


async def test_short_address_is_rejected(hass: HomeAssistant) -> None:
    """Fewer than 3 characters after stripping is too short; no search runs."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(SEARCH, return_value=ADDRESSES) as search:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address": "  ab  "}
        )
    assert result["step_id"] == "user"
    assert result["errors"] == {"address": "too_short"}
    search.assert_not_called()


async def test_address_is_stripped(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    with patch(SEARCH, return_value=ADDRESSES) as search:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"address": "  Celjska 5  "}
        )
    assert result["step_id"] == "pick"
    search.assert_called_once_with("Celjska 5")


async def test_pick_list_is_capped_at_50(hass: HomeAssistant) -> None:
    many = [SimbioAddress(str(i), f"CESTA {i}", "CELJE") for i in range(80)]
    result = await _start(hass, many)
    assert result["step_id"] == "pick"
    options = result["data_schema"].schema["address_id"].config["options"]
    assert [o["value"] for o in options] == [str(i) for i in range(50)]


async def test_last_step_flags_user_errors(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"address": "ab"}
    )
    assert result["errors"] == {"address": "too_short"}
    assert result["last_step"] is False

