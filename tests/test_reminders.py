"""Tests for the reminder switches, the scheduler and the messages."""

from __future__ import annotations

from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    async_mock_service,
    mock_restore_cache,
)

from custom_components.bin_day.const import (
    CONF_COLLECTION_TIME,
    CONF_MISSING_WARNING,
    CONF_NOTIFY_TARGETS,
    CONF_REMINDERS_ENABLED,
    CONF_TYPES,
)
from custom_components.bin_day.reminders import build_message
from custom_components.bin_day.source import SourceError

from .test_coordinator import (  # noqa: F401
    FakeSource,
    at,
    d,
    make_entry,
    set_time_zone,
    setup,
    tick,
    unload,
)

EN_MISSING = "Waste collection data is missing. Check the source."
SL_MISSING = "Podatki o odvozu smeti manjkajo. Preveri vir podatkov."
EN_ESTIMATE = "* estimate – no data from the source"
SL_ESTIMATE = "* ocena – vir nima podatka"


def reminder_options(**extra) -> dict:
    return {
        CONF_REMINDERS_ENABLED: True,
        CONF_NOTIFY_TARGETS: ["phone_a", "phone_b"],
        **extra,
    }


def switch_id(hass: HomeAssistant, entry, key: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "switch", "bin_day", f"{entry.entry_id}_reminder_{key}"
    )
    assert entity_id is not None
    return entity_id


async def set_switch(hass: HomeAssistant, entity_id: str, on: bool) -> None:
    await hass.services.async_call(
        "switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


def messages(calls) -> list[str]:
    return [call.data["message"] for call in calls]


def test_message_texts() -> None:
    assert (
        build_message("sl", "tomorrow", ["Mešani komunalni odpadki"], None)
        == "Jutri odvoz: Mešani komunalni odpadki"
    )
    assert build_message("en", "today", ["Mixed waste"], "11:00") == "Today: Mixed waste (until 11:00)"
    assert build_message("en", "today", ["Mixed waste"], None) == "Today: Mixed waste"
    assert build_message("sl", "today", ["Embalaža"], "11:00") == "Danes odvoz: Embalaža (do 11:00)"
    assert build_message("en", "tomorrow", ["Mixed waste"], None) == "Tomorrow: Mixed waste"
    estimate = build_message("en", "tomorrow", ["Mixed waste", "Packaging*"], None)
    assert estimate.split("\n") == ["Tomorrow: Mixed waste, Packaging*", EN_ESTIMATE]
    estimate_sl = build_message("sl", "tomorrow", ["Embalaža*"], None)
    assert estimate_sl.split("\n") == ["Jutri odvoz: Embalaža*", SL_ESTIMATE]


async def test_evening_sends_to_all_targets(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls_a = async_mock_service(hass, "notify", "phone_a")
    calls_b = async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    assert calls_a == [] and calls_b == []

    await tick(hass, freezer, at(12, 19))
    assert len(calls_a) == 1 and len(calls_b) == 1
    for call in (calls_a[0], calls_b[0]):
        assert call.data["title"] == "Ljubljanska cesta 1 A"
        assert call.data["message"] == "Tomorrow: Mixed waste"
    await unload(hass, entry)


async def test_message_two_types(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    calls_a = async_mock_service(hass, "notify", "phone_a")
    calls_b = async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "emb": [d(13)]}))
    await tick(hass, freezer, at(12, 19))
    assert messages(calls_a) == ["Tomorrow: Mixed waste, Packaging"]
    assert messages(calls_b) == ["Tomorrow: Mixed waste, Packaging"]
    await unload(hass, entry)


async def test_estimate_name_and_line(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.config.language = "sl"
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(9, 9))
    entry = make_entry(reminder_options())
    source = FakeSource({"emb": [d(13)]})
    assert await setup(hass, entry, source)
    source.result = {"emb": []}
    freezer.move_to(at(26, 18, 45))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    calls.clear()
    await tick(hass, freezer, at(26, 19))
    assert messages(calls) == [f"Jutri odvoz: Packaging*\n{SL_ESTIMATE}"]
    await unload(hass, entry)


async def test_no_due_types_no_message(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(10, 18, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(10, 19))
    assert calls == []
    await unload(hass, entry)


async def test_switch_off_blocks(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    evening = switch_id(hass, entry, "evening")
    assert hass.states.get(evening).state == "on"
    await set_switch(hass, evening, False)
    assert hass.states.get(evening).state == "off"
    await tick(hass, freezer, at(12, 19))
    assert calls == []
    await unload(hass, entry)


async def test_day_before_default_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 7, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "emb": [d(14)]}))
    day_before = switch_id(hass, entry, "day_before")
    assert hass.states.get(day_before).state == "off"
    await tick(hass, freezer, at(12, 8))
    assert calls == []

    await set_switch(hass, day_before, True)
    await set_switch(hass, switch_id(hass, entry, "evening"), False)
    await set_switch(hass, switch_id(hass, entry, "morning"), False)
    await tick(hass, freezer, at(13, 8))
    assert messages(calls) == ["Tomorrow: Packaging"]
    await unload(hass, entry)


async def test_morning_today(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(13, 7, 15))
    entry = make_entry(reminder_options(**{CONF_COLLECTION_TIME: "11:00"}))
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(13, 7, 30))
    assert messages(calls) == ["Today: Mixed waste (until 11:00)"]
    await unload(hass, entry)


async def test_morning_skipped_when_done(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(13, 7, 0))
    entry = make_entry(reminder_options(**{CONF_COLLECTION_TIME: "06:00"}))
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(13, 7, 30))
    assert calls == []
    await unload(hass, entry)


async def test_missing_warning_once(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(9, 9))
    options = reminder_options(**{CONF_TYPES: {"mko": {"estimate": False}}})
    entry = make_entry(options)
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator = entry.runtime_data

    source.result = {"mko": []}
    freezer.move_to(at(14, 18, 45))
    await coordinator.async_fetch_now()
    await hass.async_block_till_done()
    assert coordinator.data.resolved["mko"].status == "no_data"
    calls.clear()

    await tick(hass, freezer, at(14, 19))
    assert messages(calls) == [EN_MISSING]
    assert coordinator.history["mko"].warned is True

    await tick(hass, freezer, at(15, 19))
    assert messages(calls) == [EN_MISSING]

    # A real date re-arms the warning; the next gap warns again.
    source.result = {"mko": [d(17)]}
    freezer.move_to(at(16, 9))
    await coordinator.async_fetch_now()
    await hass.async_block_till_done()
    assert coordinator.history["mko"].warned is False
    source.result = {"mko": []}
    freezer.move_to(at(18, 18, 45))
    await coordinator.async_fetch_now()
    await hass.async_block_till_done()
    calls.clear()
    await tick(hass, freezer, at(18, 19))
    assert messages(calls) == [EN_MISSING]
    await unload(hass, entry)


async def test_missing_warning_runs_with_evening_switch_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(9, 9))
    entry = make_entry(reminder_options(**{CONF_TYPES: {"mko": {"estimate": False}}}))
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    await set_switch(hass, switch_id(hass, entry, "evening"), False)
    source.result = {"mko": []}
    freezer.move_to(at(14, 18, 45))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    calls.clear()
    await tick(hass, freezer, at(14, 19))
    assert messages(calls) == [EN_MISSING]
    await unload(hass, entry)


async def test_missing_warning_off(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(9, 9))
    entry = make_entry(
        reminder_options(
            **{CONF_TYPES: {"mko": {"estimate": False}}, CONF_MISSING_WARNING: False}
        )
    )
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    source.result = {"mko": []}
    freezer.move_to(at(14, 18, 45))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    calls.clear()
    await tick(hass, freezer, at(14, 19))
    assert calls == []
    await unload(hass, entry)


async def test_never_type_no_warning(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "bio": []}))
    assert entry.runtime_data.data.resolved["bio"].status == "never"
    await tick(hass, freezer, at(12, 19))
    assert messages(calls) == ["Tomorrow: Mixed waste"]
    await unload(hass, entry)


async def test_source_down_warning_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    hass.config.language = "sl"
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(9, 9))
    entry = make_entry(reminder_options(**{CONF_TYPES: {"mko": {"estimate": False}}}))
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator = entry.runtime_data

    source.result = SourceError("HTTP 503")
    freezer.move_to(at(14, 18, 45))
    await coordinator.async_fetch_now()
    await hass.async_block_till_done()
    assert coordinator.data.source_ok is False
    calls.clear()

    await tick(hass, freezer, at(14, 19))
    assert messages(calls) == [SL_MISSING]
    assert coordinator.source_warned is True
    stored = hass_storage[f"bin_day.{entry.entry_id}"]["data"]
    assert stored["source_warned"] is True

    await tick(hass, freezer, at(15, 19))
    assert messages(calls) == [SL_MISSING]

    # A successful fetch with a date resets the flag.
    source.result = {"mko": [d(20)]}
    freezer.move_to(at(16, 9))
    await coordinator.async_fetch_now()
    await hass.async_block_till_done()
    assert coordinator.source_warned is False
    assert hass_storage[f"bin_day.{entry.entry_id}"]["data"]["source_warned"] is False
    await unload(hass, entry)


async def test_missing_notify_target_does_not_block_others(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, caplog
) -> None:
    calls = async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(
        {CONF_REMINDERS_ENABLED: True, CONF_NOTIFY_TARGETS: ["gone_phone", "phone_b"]}
    )
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(12, 19))
    assert messages(calls) == ["Tomorrow: Mixed waste"]
    assert "gone_phone" in caplog.text
    assert any(
        rec.levelname == "WARNING" and "gone_phone" in rec.getMessage()
        for rec in caplog.records
    )
    await unload(hass, entry)


async def test_switch_restores_state(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    mock_restore_cache(
        hass, [State("switch.ljubljanska_cesta_1_a_evening_reminder", "off")]
    )
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    evening = switch_id(hass, entry, "evening")
    assert evening == "switch.ljubljanska_cesta_1_a_evening_reminder"
    assert hass.states.get(evening).state == "off"
    await tick(hass, freezer, at(12, 19))
    assert calls == []
    await unload(hass, entry)


async def test_switches_only_when_enabled(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(12, 9))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    registry = er.async_get(hass)
    assert registry.async_get_entity_id(
        "switch", "bin_day", f"{entry.entry_id}_reminder_evening"
    ) is None
    assert hass.states.async_entity_ids("switch") == []

    source = FakeSource({"mko": [d(13)]})
    with patch("custom_components.bin_day._create_source", return_value=source):
        # Turning reminders on reloads the entry and adds the switches.
        hass.config_entries.async_update_entry(entry, options=reminder_options())
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert len(hass.states.async_entity_ids("switch")) == 3

        # Turning them off again unloads exactly the loaded platforms.
        hass.config_entries.async_update_entry(entry, options={})
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert all(
        hass.states.get(eid).state == "unavailable"
        for eid in hass.states.async_entity_ids("switch")
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
