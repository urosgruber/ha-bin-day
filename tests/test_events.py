"""Tests for the bin_day_reminder and bin_day_data_missing events."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_mock_service,
)

from custom_components.bin_day.const import (
    CONF_COLLECTION_TIME,
    CONF_LANGUAGE,
    CONF_NOTIFY_TARGETS,
    CONF_REMINDERS_ENABLED,
    CONF_TYPES,
    EVENT_DATA_MISSING,
    EVENT_REMINDER,
)
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
from .test_reminders import (
    EN_MISSING,
    SL_ESTIMATE,
    messages,
    reminder_options,
    set_switch,
    switch_id,
)

TITLE = "Ljubljanska cesta 1 A"


def events_only(**extra) -> dict:
    return {CONF_REMINDERS_ENABLED: True, CONF_NOTIFY_TARGETS: [], **extra}


async def test_evening_event_payload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(reminder_options(**{CONF_COLLECTION_TIME: "11:00"}))
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(12, 19))
    assert len(events) == 1
    assert events[0].data == {
        "entry_id": entry.entry_id,
        "location": TITLE,
        "kind": "evening",
        "date": "2026-11-13",
        "types": [{"key": "mko", "name": "Mixed waste", "estimate": False}],
        "title": TITLE,
        "message": "Tomorrow: Mixed waste",
        "until": None,
    }
    await unload(hass, entry)


async def test_morning_event_payload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(13, 7, 15))
    entry = make_entry(events_only(**{CONF_COLLECTION_TIME: "11:00"}))
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(13, 7, 30))
    assert len(events) == 1
    data = events[0].data
    assert data["kind"] == "morning"
    assert data["date"] == "2026-11-13"
    assert data["message"] == "Today: Mixed waste (until 11:00)"
    assert data["until"] == "11:00"
    await unload(hass, entry)


async def test_day_before_event_payload(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(12, 7, 45))
    entry = make_entry(events_only())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await set_switch(hass, switch_id(hass, entry, "day_before"), True)
    await tick(hass, freezer, at(12, 8))
    assert [e.data["kind"] for e in events] == ["day_before"]
    assert events[0].data["date"] == "2026-11-13"
    assert events[0].data["message"] == "Tomorrow: Mixed waste"
    await unload(hass, entry)


async def test_event_with_zero_targets(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(events_only())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(12, 19))
    assert len(events) == 1
    await unload(hass, entry)


async def test_no_event_when_switch_off(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(events_only())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await set_switch(hass, switch_id(hass, entry, "evening"), False)
    await tick(hass, freezer, at(12, 19))
    assert events == []
    await unload(hass, entry)


async def test_no_event_when_reminders_disabled(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(12, 18, 45))
    entry = make_entry({CONF_REMINDERS_ENABLED: False})
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(12, 19))
    assert events == []
    await unload(hass, entry)


async def test_event_fired_when_a_target_raises(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)

    async def _fail(call: ServiceCall) -> None:
        raise HomeAssistantError("phone offline")

    hass.services.async_register("notify", "phone_a", _fail)
    calls_b = async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(reminder_options())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(12, 19))
    assert len(events) == 1
    assert messages(calls_b) == ["Tomorrow: Mixed waste"]
    await unload(hass, entry)


async def test_two_types_one_event(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(12, 18, 45))
    entry = make_entry(events_only())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "emb": [d(13)]}))
    await tick(hass, freezer, at(12, 19))
    assert len(events) == 1
    assert events[0].data["types"] == [
        {"key": "mko", "name": "Mixed waste", "estimate": False},
        {"key": "emb", "name": "Packaging", "estimate": False},
    ]
    assert events[0].data["message"] == "Tomorrow: Mixed waste, Packaging"
    await unload(hass, entry)


async def test_estimate_event_in_entry_language(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(9, 9))
    entry = make_entry(events_only(**{CONF_LANGUAGE: "sl"}))
    source = FakeSource({"emb": [d(13)]})
    assert await setup(hass, entry, source)
    source.result = {"emb": []}
    freezer.move_to(at(26, 18, 45))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    await tick(hass, freezer, at(26, 19))
    assert len(events) == 1
    assert events[0].data["types"] == [
        {"key": "emb", "name": "Packaging", "estimate": True}
    ]
    assert events[0].data["message"] == f"Jutri odvoz: Packaging*\n{SL_ESTIMATE}"
    await unload(hass, entry)


async def test_no_event_when_nothing_due(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_REMINDER)
    freezer.move_to(at(10, 18, 45))
    entry = make_entry(events_only())
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await tick(hass, freezer, at(10, 19))
    assert events == []
    await unload(hass, entry)


async def test_missing_event_once_like_the_warning(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_DATA_MISSING)
    calls = async_mock_service(hass, "notify", "phone_a")
    async_mock_service(hass, "notify", "phone_b")
    freezer.move_to(at(9, 9))
    entry = make_entry(reminder_options(**{CONF_TYPES: {"mko": {"estimate": False}}}))
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator = entry.runtime_data
    source.result = {"mko": []}
    freezer.move_to(at(14, 18, 45))
    await coordinator.async_fetch_now()
    await hass.async_block_till_done()
    calls.clear()

    await tick(hass, freezer, at(14, 19))
    assert messages(calls) == [EN_MISSING]
    assert len(events) == 1
    assert events[0].data == {
        "entry_id": entry.entry_id,
        "location": TITLE,
        "types": [{"key": "mko", "name": "Mixed waste"}],
        "source": False,
    }
    await tick(hass, freezer, at(15, 19))
    assert len(events) == 1
    await unload(hass, entry)


async def test_missing_event_source_down_with_zero_targets(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    events = async_capture_events(hass, EVENT_DATA_MISSING)
    freezer.move_to(at(9, 9))
    entry = make_entry(events_only(**{CONF_TYPES: {"mko": {"estimate": False}}}))
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator = entry.runtime_data
    source.result = SourceError("HTTP 503")
    freezer.move_to(at(14, 18, 45))
    await coordinator.async_fetch_now()
    await hass.async_block_till_done()

    await tick(hass, freezer, at(14, 19))
    assert len(events) == 1
    assert events[0].data["source"] is True
    assert events[0].data["types"] == [{"key": "mko", "name": "Mixed waste"}]
    # With no targets the event counts as delivered: no repeat next evening.
    assert coordinator.source_warned is True
    assert coordinator.history["mko"].warned is True
    assert hass_storage[f"bin_day.{entry.entry_id}"]["data"]["source_warned"] is True
    await tick(hass, freezer, at(15, 19))
    assert len(events) == 1
    await unload(hass, entry)


async def test_missing_event_repeats_while_all_targets_fail(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    events = async_capture_events(hass, EVENT_DATA_MISSING)
    freezer.move_to(at(9, 9))
    # The targets do not exist: every send fails, so the warning is retried.
    entry = make_entry(reminder_options(**{CONF_TYPES: {"mko": {"estimate": False}}}))
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    source.result = {"mko": []}
    freezer.move_to(at(14, 18, 45))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    await tick(hass, freezer, at(14, 19))
    await tick(hass, freezer, at(15, 19))
    assert len(events) == 2
    assert entry.runtime_data.history["mko"].warned is False
    await unload(hass, entry)
