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

from .test_config_flow import _add_phones
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


ORDER = ["mko", "emb", "bio"]


def _slots(result) -> list[str]:
    """Return the section names of the types form, in order."""
    return [str(k) for k in result["data_schema"].schema]


def _section_fields(result, slot: str) -> dict:
    """Return the fields of one section as {name: marker}."""
    marker = next(k for k in result["data_schema"].schema if str(k) == slot)
    section = result["data_schema"].schema[marker]
    return {str(k): k for k in section.schema.schema}


def _suggested(result, slot: str) -> dict:
    """Return the suggested values of the fields of one section."""
    return {
        name: marker.description["suggested_value"]
        for name, marker in _section_fields(result, slot).items()
        if marker.description and "suggested_value" in marker.description
    }


def _collapsed(result) -> dict[str, bool]:
    return {
        str(k): v.options["collapsed"] for k, v in result["data_schema"].schema.items()
    }


def _types_input(entry, edits: dict[str, dict] | None = None) -> dict:
    """Return the full input of the types form: current values plus edits."""
    types = get_types(entry)
    data: dict = {}
    for index, key in enumerate(ORDER):
        conf = types[key]
        values = {
            "name": conf["name"],
            "icon": conf["icon"],
            "color": conf["color"],
            "estimate": conf["estimate"],
            **((edits or {}).get(key) or {}),
        }
        if "interval" not in values and conf.get("interval") is not None:
            values["interval"] = conf["interval"]
        data[f"type_{index + 1}"] = values
    return data


async def test_options_types_change_color_and_interval(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({"emb": []})):
        result = await _menu(hass, entry, "types")
        assert result["step_id"] == "types"
        flow_id = result["flow_id"]
        edit = {"color": "#ffaa00", "estimate": False}

        # Interval out of range -> error on the section.
        result = await hass.config_entries.options.async_configure(
            flow_id, _types_input(entry, {"emb": {**edit, "interval": 40}})
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"type_2": "invalid_interval"}
        # Bad colour -> error on the section.
        result = await hass.config_entries.options.async_configure(
            flow_id, _types_input(entry, {"emb": {**edit, "color": "red", "interval": 21}})
        )
        assert result["errors"] == {"type_2": "invalid_color"}
        # Empty name -> error on the section.
        result = await hass.config_entries.options.async_configure(
            flow_id, _types_input(entry, {"emb": {**edit, "name": " "}})
        )
        assert result["errors"] == {"type_2": "invalid_name"}

        result = await hass.config_entries.options.async_configure(
            flow_id, _types_input(entry, {"emb": {**edit, "interval": 21}})
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    emb = get_types(entry)["emb"]
    assert emb["color"] == "#ffaa00"
    assert emb["interval"] == 21
    assert emb["estimate"] is False
    assert get_types(entry)["mko"]["color"] == "#00a136"

    # Empty interval -> None (learned).
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({"emb": []})):
        result = await _menu(hass, entry, "types")
        data = _types_input(entry, {"emb": {"estimate": True}})
        del data["type_2"]["interval"]
        result = await hass.config_entries.options.async_configure(result["flow_id"], data)
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
                _reminder_input(targets=["light.x"]),
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
                "evening": {"on": True, "time": "18:45"},
                "morning": {"on": True, "time": "07:00"},
                "day_before": {"on": False, "time": "08:30"},
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


async def test_options_reminders_enabled_without_targets_means_events_only(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Reminders on with no target is valid: Bin Day fires only the events."""
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _reminder_input(
            targets=[], times={"evening": "18:45", "morning": "07:00", "day_before": "08:30"}
        ),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_REMINDERS_ENABLED] is True
    assert entry.options[CONF_NOTIFY_TARGETS] == []
    await unload(hass, entry)


async def test_options_reminders_defaults(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    assert _form_defaults(result)[CONF_REMINDERS_ENABLED] is False
    assert _section_defaults(result, "evening")["time"] == "19:00"
    assert _section_defaults(result, "morning")["time"] == "07:30"
    assert _section_defaults(result, "day_before")["time"] == "08:00"
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_time_is_a_15_minute_grid(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    for name in ("evening", "morning", "day_before"):
        options = _section_options(result, name, "time")
        assert len(options) == 96
        assert options[0] == "00:00"
        assert options[1] == "00:15"
        assert options[-1] == "23:45"
        assert options == sorted(options)
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_saved_off_grid_time_is_kept_in_order(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer, {CONF_MORNING_TIME: "07:40"})
    result = await _menu(hass, entry, "reminders")
    morning = _section_options(result, "morning", "time")
    assert len(morning) == 97
    assert morning[morning.index("07:30") : morning.index("07:30") + 3] == [
        "07:30",
        "07:40",
        "07:45",
    ]
    assert morning == sorted(morning)
    assert _section_defaults(result, "morning")["time"] == "07:40"
    # The other sections keep the plain grid.
    assert len(_section_options(result, "evening", "time")) == 96
    # Submitting the form unchanged keeps the saved time.
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _reminder_input(targets=[], times={"morning": "07:40"})
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert entry.options[CONF_MORNING_TIME] == "07:40"
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
    result = await _menu(hass, entry, "types")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], _types_input(entry, {key: fields})
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


async def test_options_steps_keep_each_other(
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
            _reminder_input(times={"evening": "18:00", "morning": "07:00"}),
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


async def test_options_form_is_a_last_step(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    for choice in ("types", "reminders", "general"):
        result = await _menu(hass, entry, choice)
        assert result["type"] is FlowResultType.FORM
        assert result.get("last_step") is not False
        hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_types_sections_follow_the_order(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The sections follow ordered_keys; the names are in the placeholders."""
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "types")
    assert _slots(result) == ["type_1", "type_2", "type_3"]
    assert result["description_placeholders"] == {
        "type_1": "Mixed waste",
        "type_2": "Packaging",
        "type_3": "Bio waste",
    }
    assert set(_section_fields(result, "type_1")) == {
        "name", "icon", "color", "estimate", "interval"
    }
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_types_sections_all_collapsed_at_first(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "types")
    assert _collapsed(result) == {"type_1": True, "type_2": True, "type_3": True}
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_types_error_opens_only_the_bad_section_and_keeps_input(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "types")
    data = _types_input(
        entry, {"emb": {"color": "red", "icon": "mdi:bottle-wine", "interval": 21}}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], data)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"type_2": "invalid_color"}
    assert _collapsed(result) == {"type_1": True, "type_2": False, "type_3": True}
    # The input of the user stays as the suggested values.
    for slot in ("type_1", "type_2", "type_3"):
        assert _suggested(result, slot) == data[slot]
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_types_sections_follow_the_order_field(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """The sections follow `order`, not the stored dict order."""
    freezer.move_to(at(9, 9))
    types = {k: {**v, "order": i} for i, (k, v) in enumerate(make_entry().data[CONF_TYPES].items())}
    # Stored dict order is alphabetical, as in a real install.
    entry = MockConfigEntry_copy(make_entry(), dict(sorted(types.items())))
    assert list(entry.data[CONF_TYPES]) == ["bio", "emb", "mko"]
    assert await setup(hass, entry, FakeSource({}))
    result = await _menu(hass, entry, "types")
    assert result["description_placeholders"] == {
        "type_1": "Mixed waste",
        "type_2": "Packaging",
        "type_3": "Bio waste",
    }
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_types_sections_known_types_in_default_order(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Without an `order` field the known types keep the Simbio order."""
    freezer.move_to(at(9, 9))
    entry = MockConfigEntry_copy(make_entry(), dict(sorted(make_entry().data[CONF_TYPES].items())))
    assert list(entry.data[CONF_TYPES]) == ["bio", "emb", "mko"]
    assert await setup(hass, entry, FakeSource({}))
    result = await _menu(hass, entry, "types")
    assert list(result["description_placeholders"].values()) == [
        "Mixed waste",
        "Packaging",
        "Bio waste",
    ]
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_edit_two_types_in_one_submit_saves_both(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "types")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            _types_input(
                entry,
                {"emb": {"color": "#aa0000", "interval": 21}, "bio": {"color": "#00bb00"}},
            ),
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    types = get_types(entry)
    assert types["emb"]["color"] == "#aa0000"
    assert types["emb"]["interval"] == 21
    assert types["bio"]["color"] == "#00bb00"
    assert types["mko"]["color"] == "#00a136"
    # The edited values show when the form opens again.
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "types")
        fields = _section_fields(result, "type_2")
        assert fields["color"].default() == "#aa0000"
        assert _suggested(result, "type_2") == {"interval": 21}
        hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_types_keep_other_override_when_not_edited(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer, {CONF_TYPES: {"emb": {"color": "#aa0000", "interval": 21}}})
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        await _save_type(hass, entry, "bio", color="#00bb00")
    assert entry.options[CONF_TYPES]["emb"] == {"color": "#aa0000", "interval": 21}
    assert list(entry.options[CONF_TYPES]) == ["emb", "bio"]
    await unload(hass, entry)


async def test_options_abort_saves_nothing(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "types")
    flow_id = result["flow_id"]
    # A submit with an error does not save, and closing the dialog saves nothing.
    result = await hass.config_entries.options.async_configure(
        flow_id, _types_input(entry, {"emb": {"color": "bad"}})
    )
    assert result["type"] is FlowResultType.FORM
    hass.config_entries.options.async_abort(flow_id)
    await hass.async_block_till_done()
    assert CONF_TYPES not in entry.options
    assert get_types(entry)["emb"]["color"] == "#ffdd00"
    await unload(hass, entry)


async def test_options_submit_without_edit_keeps_options(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    entry = await _entry(hass, freezer, {CONF_MISSING_WARNING: False})
    before = dict(entry.options)
    with patch("custom_components.bin_day._create_source", return_value=FakeSource({})):
        result = await _menu(hass, entry, "types")
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], _types_input(entry)
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        # The options stay exactly as they were: no empty types key is added.
        assert result["data"] == before
    assert entry.options[CONF_MISSING_WARNING] is False
    await unload(hass, entry)


# ----- reminder switches in the reminders form -----

def _section_defaults(result, name: str) -> dict[str, object]:
    """Return the defaults of the fields in one section."""
    marker = next(k for k in result["data_schema"].schema if str(k) == name)
    section = result["data_schema"].schema[marker]
    return {str(k): k.default() for k in section.schema.schema if callable(k.default)}


def _section_options(result, name: str, field: str) -> list[str]:
    marker = next(k for k in result["data_schema"].schema if str(k) == name)
    section = result["data_schema"].schema[marker]
    key = next(k for k in section.schema.schema if str(k) == field)
    return list(section.schema.schema[key].config["options"])


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


def _reminder_input(
    enabled: bool = True,
    targets: list[str] | None = None,
    on: dict[str, bool] | None = None,
    times: dict[str, str] | None = None,
) -> dict:
    """Return the input of the reminders form."""
    on = {"evening": True, "morning": True, "day_before": False, **(on or {})}
    times = {"evening": "19:00", "morning": "07:30", "day_before": "08:00", **(times or {})}
    return {
        CONF_REMINDERS_ENABLED: enabled,
        CONF_NOTIFY_TARGETS: ["mobile_app_one"] if targets is None else targets,
        **{key: {"on": on[key], "time": times[key]} for key in on},
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
        "evening",
        "morning",
        "day_before",
    ]
    assert _section_defaults(result, "evening")["on"] is True
    assert _section_defaults(result, "morning")["on"] is True
    assert _section_defaults(result, "day_before")["on"] is False
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
    assert _section_defaults(result, "evening")["on"] is False
    assert _section_defaults(result, "morning")["on"] is True
    assert _section_defaults(result, "day_before")["on"] is True
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
            _reminder_input(on={"evening": False, "morning": True, "day_before": True}),
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert hass.states.get(evening).state == "off"
    assert hass.states.get(switch_id(hass, entry, "morning")).state == "on"
    assert hass.states.get(day_before).state == "on"
    for key in ("evening", "morning", "day_before", CONF_INITIAL_SWITCH_STATES):
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
            _reminder_input(on={"evening": False, "morning": True, "day_before": True}),
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert entry.options[CONF_INITIAL_SWITCH_STATES] == {
        "evening": False,
        "morning": True,
        "day_before": True,
    }
    for key in ("evening", "morning", "day_before"):
        assert key not in entry.options
    # The new switches start with the wanted states.
    assert hass.states.get(switch_id(hass, entry, "evening")).state == "off"
    assert hass.states.get(switch_id(hass, entry, "morning")).state == "on"
    assert hass.states.get(switch_id(hass, entry, "day_before")).state == "on"
    await unload(hass, entry)


def test_new_option_strings_exist_in_every_language() -> None:
    import json
    from pathlib import Path

    base = Path(__file__).parent.parent / "custom_components" / "bin_day"
    for name in ("strings.json", "translations/en.json", "translations/sl.json"):
        raw = json.loads((base / name).read_text())
        step = raw["options"]["step"]
        reminders = step["reminders"]
        assert set(reminders["data"]) == {"reminders_enabled", "notify_targets"}, name
        assert set(reminders["data_description"]) == {"reminders_enabled", "notify_targets"}, name
        assert set(reminders["sections"]) == {"evening", "morning", "day_before"}, name
        for section in reminders["sections"].values():
            assert section["name"], name
            assert set(section["data"]) == {"on", "time"}, name
        # One section for each type slot; the picker step is gone.
        assert "type" not in step, name
        sections = step["types"]["sections"]
        assert set(sections) == {f"type_{n}" for n in range(1, 11)}, name
        for number, section in sections.items():
            assert section["name"] == "{" + number + "}", name
            assert set(section["data"]) == {
                "name", "icon", "color", "estimate", "interval"
            }, name
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
                enabled=False, on={"evening": False, "morning": True, "day_before": True}
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
        assert _section_defaults(result, "evening")["on"] is False
        assert _section_defaults(result, "morning")["on"] is True
        assert _section_defaults(result, "day_before")["on"] is True
        hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


def _fake_phones(hass: HomeAssistant) -> None:
    """Fake one iOS phone of a person and one laptop; only the phone is a target."""
    _add_phones(hass, [("iOS", "Phone A"), ("Linux", "Box")])


def _target_default(result) -> list[str]:
    schema = result["data_schema"].schema
    key = next(k for k in schema if str(k) == CONF_NOTIFY_TARGETS)
    return key.default()


@pytest.mark.parametrize("saved", [None, []])
async def test_options_reminders_prefill_phones_when_no_saved_targets(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, saved
) -> None:
    _fake_phones(hass)
    options = {} if saved is None else {CONF_NOTIFY_TARGETS: saved}
    entry = await _entry(hass, freezer, options)
    result = await _menu(hass, entry, "reminders")
    assert _target_default(result) == ["mobile_app_phone_a"]
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_events_only_is_not_prefilled(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """A saved "events only" setup reopens with no phone selected."""
    _fake_phones(hass)
    entry = await _entry(
        hass, freezer, {CONF_REMINDERS_ENABLED: True, CONF_NOTIFY_TARGETS: []}
    )
    result = await _menu(hass, entry, "reminders")
    assert _target_default(result) == []
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_saved_targets_beat_phones(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _fake_phones(hass)
    entry = await _entry(hass, freezer, {CONF_NOTIFY_TARGETS: ["mobile_app_box"]})
    result = await _menu(hass, entry, "reminders")
    assert _target_default(result) == ["mobile_app_box"]
    hass.config_entries.options.async_abort(result["flow_id"])
    await unload(hass, entry)


async def test_options_reminders_empty_submit_stays_empty(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    _fake_phones(hass)
    entry = await _entry(hass, freezer)
    result = await _menu(hass, entry, "reminders")
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        _reminder_input(targets=[]),
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options[CONF_REMINDERS_ENABLED] is True
    assert entry.options[CONF_NOTIFY_TARGETS] == []
    await unload(hass, entry)
