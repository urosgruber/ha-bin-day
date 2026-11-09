"""Tests for the options flow."""

from __future__ import annotations

from unittest.mock import patch

import voluptuous as vol
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.bin_day.const import (
    CONF_COLLECTION_TIME,
    CONF_DAY_BEFORE_TIME,
    CONF_EVENING_TIME,
    CONF_MISSING_WARNING,
    CONF_MORNING_TIME,
    CONF_NOTIFY_TARGETS,
    CONF_REMINDERS_ENABLED,
    CONF_TYPES,
)
from custom_components.bin_day.coordinator import get_types

from .test_coordinator import FakeSource, at, d, make_entry, set_time_zone, setup, unload  # noqa: F401


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
    assert labels == {"mko": "Mixed waste", "emb": "Packaging", "bio": "Bio waste"}
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
