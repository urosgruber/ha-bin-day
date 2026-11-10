"""Tests for the options flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bin_day.const import (
    CONF_INITIAL_SWITCH_STATES,
    CONF_COLLECTION_TIME,
    CONF_DAY_BEFORE_TIME,
    CONF_EVENING_TIME,
    CONF_LANGUAGE,
    CONF_MISSING_WARNING,
    CONF_MORNING_TIME,
    CONF_NOTIFY_TARGETS,
    CONF_REMINDERS_ENABLED,
    CONF_TYPES,
)
from custom_components.bin_day.coordinator import get_types

from .test_reminders import switch_id
from .test_coordinator import FakeSource, at, d, make_entry, set_time_zone, setup, unload  # noqa: F401


def MockConfigEntry_copy(entry, types: dict):
    """Return a copy of the entry with the types in the given dict order."""
    return MockConfigEntry(
        domain=entry.domain,
        title=entry.title,
        data={**entry.data, CONF_TYPES: types},
        options=dict(entry.options),
    )


async def _menu(hass: HomeAssistant, entry, choice: str):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["types", "reminders", "general"]
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": choice}
    )


async def _entry(hass: HomeAssistant, freezer: FrozenDateTimeFactory, options=None):
    freezer.move_to(at(9, 9))
    entry = make_entry(options)
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "emb": [d(20)], "bio": []}))
    return entry


async def _pick_type(hass: HomeAssistant, entry, key: str):
    result = await _menu(hass, entry, "types")
    assert result["step_id"] == "types"
    labels = {o["value"]: o["label"] for o in result["data_schema"].schema["type"].config["options"]}
    assert labels == {
        "mko": "Mixed waste",
        "emb": "Packaging",
        "bio": "Bio waste",
        "done": "Save and close",
    }
    return await hass.config_entries.options.async_configure(result["flow_id"], {"type": key})


async def test_options_types_change_color_and_interval(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({"emb": []})):
        result = await _pick_type(hass, entry, "emb")
        assert result["step_id"] == "type"
        assert result["description_placeholders"] == {"type": "Packaging"}
        flow_id = result["flow_id"]
        base = {"name": "Packaging", "icon": "mdi:recycle", "color": "#ffaa00", "estimate": False}

        # Interval out of range -> error on the field, input kept.
        result = await hass.config_entries.options.async_configure(
            flow_id, {**base, "interval": 40}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"interval": "invalid_interval"}
        # Bad colour -> error on the field.
        result = await hass.config_entries.options.async_configure(
            flow_id, {**base, "color": "red", "interval": 21}
        )
        assert result["errors"] == {"color": "invalid_color"}

        result = await hass.config_entries.options.async_configure(
            flow_id, {**base, "interval": 21}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "types"
        result = await hass.config_entries.options.async_configure(flow_id, {"type": "done"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    emb = get_types(entry)["emb"]
    assert emb["color"] == "#ffaa00"
    assert emb["interval"] == 21
    assert emb["estimate"] is False
    assert get_types(entry)["mko"]["color"] == "#00a136"

    # Empty interval -> None (learned); other saved types are kept.
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({"emb": []})):
        result = await _pick_type(hass, entry, "emb")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"name": "Packaging", "icon": "mdi:recycle", "color": "#ffaa00", "estimate": True},
        )
        assert result["step_id"] == "types"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"type": "done"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert get_types(entry)["emb"]["interval"] is None
    assert get_types(entry)["emb"]["estimate"] is True
    await unload(hass, entry)


async def test_options_interval_used_for_estimate(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry({CONF_TYPES: {"emb": {"interval": 21}}})
    source = FakeSource({"emb": [d(13)]})
    assert await setup(hass, entry, source)
    source.result = {"emb": []}
    freezer.move_to(at(16, 9))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.ljubljanska_cesta_1_a_packaging")
    assert state is not None
    assert state.state == "2026-12-04"
    assert state.attributes["estimate"] is True
    await unload(hass, entry)


async def test_options_reminders(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    async def _noop(call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_one", _noop)
    hass.services.async_register("notify", "mobile_app_two", _noop)
    hass.services.async_register("notify", "send_message", _noop)
    hass.services.async_register("notify", "persistent_notification", _noop)
    entry = await _entry(
        hass, freezer, {CONF_NOTIFY_TARGETS: ["mobile_app_gone", "mobile_app_one"]}
    )
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "reminders")
        assert result["step_id"] == "reminders"
        schema = result["data_schema"].schema
        offered = [o["value"] for o in schema[CONF_NOTIFY_TARGETS].config["options"]]
        assert offered == ["mobile_app_one", "mobile_app_two"]
        flow_id = result["flow_id"]

        # A target that is not an offered notify service is rejected.
        try:
            await hass.config_entries.options.async_configure(
                flow_id,
                {CONF_REMINDERS_ENABLED: True, CONF_NOTIFY_TARGETS: ["light.x"]},
            )
        except (InvalidData, vol.Invalid):
            pass
        else:
            raise AssertionError("expected a schema error")

        result = await hass.config_entries.options.async_configure(
            flow_id,
            {
                CONF_REMINDERS_ENABLED: True,
                CONF_NOTIFY_TARGETS: ["mobile_app_two"],
                CONF_EVENING_TIME: "18:45:00",
                CONF_MORNING_TIME: "07:00:00",
                CONF_DAY_BEFORE_TIME: "08:30:00",
            },
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert entry.options[CONF_REMINDERS_ENABLED] is True
    assert entry.options[CONF_NOTIFY_TARGETS] == ["mobile_app_two"]
    assert entry.options[CONF_EVENING_TIME] == "18:45"
    assert entry.options[CONF_MORNING_TIME] == "07:00"
    assert entry.options[CONF_DAY_BEFORE_TIME] == "08:30"
    await unload(hass, entry)


async def test_options_reminders_enabled_needs_a_target(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Reminders on with no target shows an error and keeps the input."""

    async def _noop(call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_one", _noop)
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_REMINDERS_ENABLED: True,
            CONF_NOTIFY_TARGETS: [],
            CONF_EVENING_TIME: "18:45:00",
            CONF_MORNING_TIME: "07:00:00",
            CONF_DAY_BEFORE_TIME: "08:30:00",
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reminders"
    assert result["errors"] == {CONF_NOTIFY_TARGETS: "no_targets"}
    suggested = {
        str(k): k.description["suggested_value"]
        for k in result["data_schema"].schema
        if k.description and "suggested_value" in k.description
    }
    assert suggested[CONF_REMINDERS_ENABLED] is True
    assert suggested[CONF_EVENING_TIME] == "18:45:00"
    assert CONF_REMINDERS_ENABLED not in entry.options
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_defaults(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    schema = result["data_schema"].schema
    defaults = {str(k): k.default() for k in schema if k.default is not None and callable(k.default)}
    assert defaults[CONF_REMINDERS_ENABLED] is False
    assert defaults[CONF_EVENING_TIME] == "19:00"
    assert defaults[CONF_MORNING_TIME] == "07:30"
    assert defaults[CONF_DAY_BEFORE_TIME] == "08:00"
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_general_collection_time(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "general")
        assert result["step_id"] == "general"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MISSING_WARNING: False, CONF_COLLECTION_TIME: "11:00:00"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.options[CONF_COLLECTION_TIME] == "11:00"
        assert entry.options[CONF_MISSING_WARNING] is False

        result = await _menu(hass, entry, "general")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MISSING_WARNING: True}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert CONF_COLLECTION_TIME not in entry.options
    assert entry.options[CONF_MISSING_WARNING] is True
    await unload(hass, entry)


async def test_options_reload_entry(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    entry = await _entry(hass, freezer)
    with patch(
        "custom_components.bin_day._create_source",
        return_value=FakeSource({"mko": [d(13)], "emb": [d(20)], "bio": []}),
    ) as create:
        result = await _menu(hass, entry, "general")
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MISSING_WARNING: False, CONF_COLLECTION_TIME: "10:00:00"}
        )
        await hass.async_block_till_done()
        assert create.called  # the entry was set up again
    assert entry.runtime_data is not None
    # The new collection time shows in the next sensor's attribute.
    states = [s for s in hass.states.async_all("sensor") if "until" in s.attributes]
    assert states and states[0].attributes["until"] == "10:00"
    await unload(hass, entry)


async def _save_type(hass, entry, key, **fields):
    result = await _pick_type(hass, entry, key)
    base = {"name": get_types(entry)[key]["name"], "icon": "mdi:recycle", "color": "#112233",
            "estimate": True}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**base, **fields}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "types"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"type": "done"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_options_editing_type_keeps_other_type_override(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        await _save_type(hass, entry, "emb", color="#aa0000", interval=21)
        await _save_type(hass, entry, "bio", color="#00bb00")
    types = get_types(entry)
    assert types["emb"]["color"] == "#aa0000"
    assert types["emb"]["interval"] == 21
    assert types["bio"]["color"] == "#00bb00"
    await unload(hass, entry)


async def test_options_sections_keep_each_other(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    async def _noop(call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_one", _noop)
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        await _save_type(hass, entry, "emb", color="#aa0000", interval=21)
        result = await _menu(hass, entry, "reminders")
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_REMINDERS_ENABLED: True, CONF_NOTIFY_TARGETS: ["mobile_app_one"],
             CONF_EVENING_TIME: "18:00:00",
             CONF_MORNING_TIME: "07:00:00", CONF_DAY_BEFORE_TIME: "08:00:00"},
        )
        await hass.async_block_till_done()
        result = await _menu(hass, entry, "general")
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MISSING_WARNING: False, CONF_COLLECTION_TIME: "11:00:00"}
        )
        await hass.async_block_till_done()
        # Reminders and general keep the type override.
        assert get_types(entry)["emb"]["interval"] == 21
        assert entry.options[CONF_REMINDERS_ENABLED] is True
        # Saving a type keeps the reminder and general options.
        await _save_type(hass, entry, "mko", color="#010101")
    assert entry.options[CONF_REMINDERS_ENABLED] is True
    assert entry.options[CONF_EVENING_TIME] == "18:00"
    assert entry.options[CONF_MISSING_WARNING] is False
    assert entry.options[CONF_COLLECTION_TIME] == "11:00"
    assert get_types(entry)["emb"]["color"] == "#aa0000"
    assert get_types(entry)["mko"]["color"] == "#010101"
    await unload(hass, entry)


async def test_options_general_language(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "general")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MISSING_WARNING: True, CONF_LANGUAGE: "sl"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.options[CONF_LANGUAGE] == "sl"

        # The form shows the saved value; an empty form falls back to "auto".
        result = await _menu(hass, entry, "general")
        keys = {str(k): k for k in result["data_schema"].schema}
        assert keys[CONF_LANGUAGE].default() == "sl"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_MISSING_WARNING: True, CONF_LANGUAGE: "auto"}
        )
        await hass.async_block_till_done()
    assert entry.options[CONF_LANGUAGE] == "auto"
    await unload(hass, entry)


async def test_options_general_language_rejects_unknown(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "general")
        with pytest.raises(InvalidData):
            await hass.config_entries.options.async_configure(
                result["flow_id"], {CONF_MISSING_WARNING: True, CONF_LANGUAGE: "de"}
            )
    await unload(hass, entry)


async def test_options_last_step_flags_and_picker_order(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "types")
    assert result["step_id"] == "types"
    assert result["last_step"] is False
    schema = result["data_schema"].schema
    marker = next(k for k in schema if str(k) == "type")
    config = schema[marker].config
    assert [o["value"] for o in config["options"]] == [*get_types(entry), "done"]
    assert config["sort"] is False
    assert marker.default() == next(iter(get_types(entry)))
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"type": "emb"}
    )
    assert result["step_id"] == "type"
    assert result["last_step"] is False
    await unload(hass, entry)


async def test_options_finishing_forms_do_not_set_last_step_false(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    for choice in ("reminders", "general"):
        result = await _menu(hass, entry, choice)
        assert result["type"] is FlowResultType.FORM
        assert result.get("last_step") is not False
        hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


def _picker(result) -> tuple[list[str], str]:
    schema = result["data_schema"].schema
    marker = next(k for k in schema if str(k) == "type")
    return [o["value"] for o in schema[marker].config["options"]], marker.default()


def _form(key_name: str) -> dict:
    return {"name": key_name, "icon": "mdi:recycle", "color": "#112233", "estimate": True}


async def test_options_picker_follows_the_order_field(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The picker lists the types by `order`, with the first one as default."""
    freezer.move_to(at(9, 9))
    types = {k: {**v, "order": i} for i, (k, v) in enumerate(make_entry().data[CONF_TYPES].items())}
    # Stored dict order is alphabetical, as in a real install.
    entry = MockConfigEntry_copy(make_entry(), dict(sorted(types.items())))
    assert list(entry.data[CONF_TYPES]) == ["bio", "emb", "mko"]
    assert await setup(hass, entry, FakeSource({}))
    result = await _menu(hass, entry, "types")
    values, default = _picker(result)
    assert values == ["mko", "emb", "bio", "done"]
    assert default == "mko"
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_picker_known_types_in_default_order(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Without an `order` field the known types keep the Simbio order."""
    freezer.move_to(at(9, 9))
    entry = MockConfigEntry_copy(make_entry(), dict(sorted(make_entry().data[CONF_TYPES].items())))
    assert list(entry.data[CONF_TYPES]) == ["bio", "emb", "mko"]
    assert await setup(hass, entry, FakeSource({}))
    result = await _menu(hass, entry, "types")
    values, default = _picker(result)
    assert values == ["mko", "emb", "bio", "done"]
    assert default == "mko"
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_edit_two_types_then_done_saves_both(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "types")
        flow_id = result["flow_id"]
        result = await hass.config_entries.options.async_configure(flow_id, {"type": "emb"})
        assert result["step_id"] == "type"
        result = await hass.config_entries.options.async_configure(
            flow_id, {**_form("Packaging"), "color": "#aa0000", "interval": 21}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "types"
        assert result["last_step"] is False
        # Nothing is saved before "done".
        assert CONF_TYPES not in entry.options

        # The edited values show when the type is picked again.
        result = await hass.config_entries.options.async_configure(flow_id, {"type": "emb"})
        suggested = {
            str(k): k.description["suggested_value"]
            for k in result["data_schema"].schema
            if k.description and "suggested_value" in k.description
        }
        defaults = {str(k): k.default() for k in result["data_schema"].schema if callable(k.default)}
        assert suggested["interval"] == 21
        assert defaults["color"] == "#aa0000"
        result = await hass.config_entries.options.async_configure(
            flow_id, {**_form("Packaging"), "color": "#aa0000", "interval": 21}
        )
        result = await hass.config_entries.options.async_configure(flow_id, {"type": "bio"})
        result = await hass.config_entries.options.async_configure(
            flow_id, {**_form("Bio waste"), "color": "#00bb00"}
        )
        assert result["step_id"] == "types"
        result = await hass.config_entries.options.async_configure(flow_id, {"type": "done"})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    types = get_types(entry)
    assert types["emb"]["color"] == "#aa0000"
    assert types["emb"]["interval"] == 21
    assert types["bio"]["color"] == "#00bb00"
    await unload(hass, entry)


async def test_options_abort_saves_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "types")
    flow_id = result["flow_id"]
    await hass.config_entries.options.async_configure(flow_id, {"type": "emb"})
    result = await hass.config_entries.options.async_configure(
        flow_id, {**_form("Packaging"), "color": "#aa0000"}
    )
    assert result["step_id"] == "types"
    hass.config_entries.options.async_abort(flow_id)
    await hass.async_block_till_done()
    assert CONF_TYPES not in entry.options
    assert get_types(entry)["emb"]["color"] == "#ffdd00"
    await unload(hass, entry)


async def test_options_done_without_edit_keeps_options(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer, {CONF_MISSING_WARNING: False})
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "types")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"type": "done"}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_MISSING_WARNING] is False
    await unload(hass, entry)


# ----- reminder switches in the reminders form -----

def _form_defaults(result) -> dict[str, object]:
    return {
        str(k): k.default()
        for k in result["data_schema"].schema
        if callable(k.default)
    }


async def _enabled_entry(hass: HomeAssistant, freezer: FrozenDateTimeFactory):
    async def _noop(call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_one", _noop)
    return await _entry(
        hass,
        freezer,
        {CONF_REMINDERS_ENABLED: True, CONF_NOTIFY_TARGETS: ["mobile_app_one"]},
    )


def _reminder_input(**changes) -> dict:
    return {
        CONF_REMINDERS_ENABLED: True,
        CONF_NOTIFY_TARGETS: ["mobile_app_one"],
        CONF_EVENING_TIME: "19:00:00",
        CONF_MORNING_TIME: "07:30:00",
        CONF_DAY_BEFORE_TIME: "08:00:00",
        **changes,
    }


async def test_options_reminders_form_order_and_defaults_without_switches(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    keys = [str(k) for k in result["data_schema"].schema]
    assert keys == [
        CONF_REMINDERS_ENABLED,
        CONF_NOTIFY_TARGETS,
        "evening_on",
        CONF_EVENING_TIME,
        "morning_on",
        CONF_MORNING_TIME,
        "day_before_on",
        CONF_DAY_BEFORE_TIME,
    ]
    defaults = _form_defaults(result)
    assert defaults["evening_on"] is True
    assert defaults["morning_on"] is True
    assert defaults["day_before_on"] is False
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_form_defaults_follow_switches(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _enabled_entry(hass, freezer)
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": switch_id(hass, entry, "evening")}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": switch_id(hass, entry, "day_before")}, blocking=True
    )
    result = await _menu(hass, entry, "reminders")
    defaults = _form_defaults(result)
    assert defaults["evening_on"] is False
    assert defaults["morning_on"] is True
    assert defaults["day_before_on"] is True
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_submit_turns_switches_and_stores_no_booleans(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _enabled_entry(hass, freezer)
    evening = switch_id(hass, entry, "evening")
    day_before = switch_id(hass, entry, "day_before")
    assert hass.states.get(evening).state == "on"
    assert hass.states.get(day_before).state == "off"
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "reminders")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _reminder_input(evening_on=False, morning_on=True, day_before_on=True),
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert hass.states.get(evening).state == "off"
    assert hass.states.get(switch_id(hass, entry, "morning")).state == "on"
    assert hass.states.get(day_before).state == "on"
    for key in ("evening_on", "morning_on", "day_before_on", CONF_INITIAL_SWITCH_STATES):
        assert key not in entry.options
    await unload(hass, entry)


async def test_options_reminders_first_enable_stores_initial_states(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    async def _noop(call: ServiceCall) -> None:
        return None

    hass.services.async_register("notify", "mobile_app_one", _noop)
    entry = await _entry(hass, freezer)
    assert hass.states.async_entity_ids("switch") == []
    with patch(
        "custom_components.bin_day._create_source",
        return_value=FakeSource({"mko": [d(13)], "emb": [d(20)], "bio": []}),
    ):
        result = await _menu(hass, entry, "reminders")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _reminder_input(evening_on=False, morning_on=True, day_before_on=True),
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert entry.options[CONF_INITIAL_SWITCH_STATES] == {
        "evening": False,
        "morning": True,
        "day_before": True,
    }
    for key in ("evening_on", "morning_on", "day_before_on"):
        assert key not in entry.options
    # The new switches start with the wanted states.
    assert hass.states.get(switch_id(hass, entry, "evening")).state == "off"
    assert hass.states.get(switch_id(hass, entry, "morning")).state == "on"
    assert hass.states.get(switch_id(hass, entry, "day_before")).state == "on"
    await unload(hass, entry)


async def test_options_reminders_no_targets_still_an_error(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _reminder_input(**{CONF_NOTIFY_TARGETS: []})
    )
    assert result["errors"] == {CONF_NOTIFY_TARGETS: "no_targets"}
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


def test_new_option_strings_exist_in_every_language() -> None:
    import json
    from pathlib import Path

    base = Path(__file__).parent.parent / "custom_components" / "bin_day"
    expected_en = {
        "evening_on": "Evening before – on",
        "morning_on": "Morning of the day – on",
        "day_before_on": "Morning the day before – on",
    }
    expected_sl = {
        "evening_on": "Večer prej – vklopljeno",
        "morning_on": "Zjutraj na dan odvoza – vklopljeno",
        "day_before_on": "Zjutraj dan prej – vklopljeno",
    }
    for name, expected in (("strings.json", expected_en), ("translations/en.json", expected_en),
                           ("translations/sl.json", expected_sl)):
        step = json.loads((base / name).read_text())["options"]["step"]
        for key, label in expected.items():
            assert step["reminders"]["data"][key] == label, (name, key)
        assert step["reminders"]["description"], name
        assert step["types"]["data"]["type"], name
    sl = json.loads((base / "translations/sl.json").read_text())
    assert "Send reminders" not in json.dumps(sl)


async def test_options_reminders_disable_keeps_wanted_switch_states(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _enabled_entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "reminders")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _reminder_input(
                **{CONF_REMINDERS_ENABLED: False},
                evening_on=False,
                morning_on=True,
                day_before_on=True,
            ),
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert entry.options[CONF_INITIAL_SWITCH_STATES] == {
            "evening": False,
            "morning": True,
            "day_before": True,
        }
        # The form shows the kept states while the switches do not exist.
        result = await _menu(hass, entry, "reminders")
        defaults = {
            str(k): k.default() for k in result["data_schema"].schema if hasattr(k, "default")
        }
        assert defaults["evening_on"] is False
        assert defaults["morning_on"] is True
        assert defaults["day_before_on"] is True
        hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)
