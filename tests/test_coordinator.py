"""Tests for the coordinator: fetch, learn, persist, fetch times and repairs."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
import pytest

from custom_components.bin_day.const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_EVENING_TIME,
    CONF_REMINDERS_ENABLED,
    CONF_TYPES,
    DOMAIN,
    RETRY_DELAY,
)
from custom_components.bin_day.coordinator import BinDayCoordinator
from custom_components.bin_day.schedule import TypeHistory
from custom_components.bin_day.source import AddressNotFound, SourceError

TZ = "Europe/Ljubljana"


def d(day: int, month: int = 11, year: int = 2026) -> date:
    return date(year, month, day)


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=dt_util.get_time_zone(TZ))


class FakeSource:
    """A source that returns a given result or raises a given error."""

    def __init__(self, result: dict[str, list[date]] | Exception) -> None:
        self.result = result
        self.calls = 0

    async def async_fetch(self) -> dict[str, list[date]]:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return {key: list(value) for key, value in self.result.items()}


TYPES = {
    "mko": {"name": "Mixed waste", "icon": "mdi:trash-can", "color": "#00a136", "estimate": True, "interval": None},
    "emb": {"name": "Packaging", "icon": "mdi:recycle", "color": "#ffdd00", "estimate": True, "interval": None},
    "bio": {"name": "Bio waste", "icon": "mdi:leaf", "color": "#8d6e63", "estimate": True, "interval": None},
}


@pytest.fixture(autouse=True)
async def set_time_zone(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone(TZ)


def make_entry(options: dict | None = None) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Ljubljanska cesta 1 A",
        data={
            CONF_ADDRESS_ID: "1",
            CONF_ADDRESS_NAME: "Ljubljanska cesta 1 A",
            CONF_CITY: "Ljubljana",
            CONF_TYPES: TYPES,
        },
        options=options or {},
    )


async def setup(hass: HomeAssistant, entry: MockConfigEntry, source: FakeSource) -> bool:
    with patch("custom_components.bin_day._create_source", return_value=source):
        if hass.config_entries.async_get_entry(entry.entry_id) is None:
            entry.add_to_hass(hass)
        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return result


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: datetime) -> None:
    freezer.move_to(when)
    async_fire_time_changed(hass, when)
    await hass.async_block_till_done()


async def unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_first_refresh_resolves_and_saves(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)], "emb": [d(20)], "bio": []})
    assert await setup(hass, entry, source)

    coordinator: BinDayCoordinator = entry.runtime_data
    assert coordinator.type_keys == ["mko", "emb", "bio"]
    data = coordinator.data
    assert data.resolved["mko"].status == "real"
    assert data.resolved["mko"].days == 4
    assert data.resolved["emb"].status == "real"
    assert data.resolved["bio"].status == "never"
    assert data.today == d(9)
    assert data.source_ok is True
    assert data.last_success is not None

    stored = hass_storage[f"bin_day.{entry.entry_id}"]
    assert stored["version"] == 1
    assert stored["data"]["types"]["mko"]["last_real"] == "2026-11-13"
    assert stored["data"]["source_warned"] is False
    await unload(hass, entry)


async def test_fetch_failure_keeps_data(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)], "emb": [d(20)], "bio": []})
    assert await setup(hass, entry, source)
    coordinator: BinDayCoordinator = entry.runtime_data
    before = dict(coordinator.data.resolved)

    source.result = SourceError("HTTP 503")
    await tick(hass, freezer, at(9, 12))  # fetch slot
    assert source.calls == 2
    assert coordinator.data.resolved == before
    assert coordinator.data.source_ok is False

    # Four retries, 15 minutes apart, belong to this slot.
    now = at(9, 12)
    for expected in (3, 4, 5, 6):
        now += RETRY_DELAY
        await tick(hass, freezer, now)
        assert source.calls == expected
    now += RETRY_DELAY
    await tick(hass, freezer, now)
    assert source.calls == 6
    assert coordinator.data.resolved == before

    # The next fetch slot starts a new count.
    source.result = {"mko": [d(13)], "emb": [d(20)], "bio": []}
    await tick(hass, freezer, at(9, 18, 30))
    assert source.calls == 7
    assert coordinator.data.source_ok is True
    await unload(hass, entry)


async def test_new_slot_cancels_pending_retry(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 11, 50))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator: BinDayCoordinator = entry.runtime_data

    source.result = SourceError("down")
    await coordinator.async_fetch_now()  # retry due at 12:05
    assert source.calls == 2
    await tick(hass, freezer, at(9, 12))  # new slot, cancels the 12:05 retry
    assert source.calls == 3
    await tick(hass, freezer, at(9, 12, 5))
    assert source.calls == 3
    await tick(hass, freezer, at(9, 12, 15))  # the retry of the 12:00 slot
    assert source.calls == 4
    await unload(hass, entry)


async def test_address_not_found_creates_issue(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator: BinDayCoordinator = entry.runtime_data
    issue_id = f"address_not_found_{entry.entry_id}"
    registry = ir.async_get(hass)

    source.result = AddressNotFound("1")
    await tick(hass, freezer, at(9, 12))
    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_key == "address_not_found"
    assert issue.translation_placeholders == {"address": "Ljubljanska cesta 1 A, Ljubljana"}
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.is_fixable is False
    assert coordinator.data.source_ok is False
    assert coordinator.data.resolved["mko"].status == "real"

    # No retries for an unknown address.
    await tick(hass, freezer, at(9, 12, 15))
    assert source.calls == 2

    source.result = {"mko": [d(13)]}
    await tick(hass, freezer, at(9, 18, 30))
    assert registry.async_get_issue(DOMAIN, issue_id) is None
    await unload(hass, entry)


async def test_history_survives_restart(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "emb": [], "bio": []}))
    await unload(hass, entry)

    freezer.move_to(at(16, 9))
    assert await setup(hass, entry, FakeSource({"mko": [], "emb": [], "bio": []}))
    res = entry.runtime_data.data.resolved["mko"]
    assert res.status == "estimate"
    assert res.date == d(27)
    await unload(hass, entry)


async def test_first_fetch_failure_with_history_does_not_fail_setup(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    await unload(hass, entry)

    freezer.move_to(at(16, 9))
    assert await setup(hass, entry, FakeSource(SourceError("down")))
    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data.data
    assert data.source_ok is False
    assert data.last_success is None
    assert data.resolved["mko"].status == "estimate"
    assert data.resolved["mko"].date == d(27)
    await unload(hass, entry)


async def test_first_fetch_failure_without_history_retries_setup(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    source = FakeSource(SourceError("down"))
    assert not await setup(hass, entry, source)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    await unload(hass, entry)


async def test_corrupt_store_entry_falls_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage, caplog
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    hass_storage[f"bin_day.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"bin_day.{entry.entry_id}",
        "data": {
            "types": {
                "mko": {"last_real": "not-a-date", "interval": 14, "ever_seen": True},
                "emb": TypeHistory(last_real=d(6), ever_seen=True).to_dict(),
            },
            "source_warned": True,
        },
    }
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "emb": [], "bio": []}))
    coordinator: BinDayCoordinator = entry.runtime_data
    assert coordinator.history["mko"].last_real == d(13)
    assert coordinator.history["emb"].last_real == d(6)
    # The fetch returned a date, so the source-down warning is re-armed.
    assert coordinator.source_warned is False
    assert "mko" in caplog.text and "corrupt" in caplog.text.lower()
    await unload(hass, entry)


async def test_source_warned_loads_and_survives_failure(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    """The stored flag loads, and a failed fetch keeps it."""
    freezer.move_to(at(9, 9))
    entry = make_entry()
    hass_storage[f"bin_day.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"bin_day.{entry.entry_id}",
        "data": {
            "types": {"mko": TypeHistory(last_real=d(6), ever_seen=True).to_dict()},
            "source_warned": True,
        },
    }
    assert await setup(hass, entry, FakeSource(SourceError("HTTP 503")))
    coordinator: BinDayCoordinator = entry.runtime_data
    assert coordinator.data.source_ok is False
    assert coordinator.source_warned is True
    await unload(hass, entry)


async def test_success_without_dates_keeps_source_warned(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Only a fetch that yields a date re-arms the source-down warning."""
    freezer.move_to(at(9, 9))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator: BinDayCoordinator = entry.runtime_data
    coordinator.source_warned = True
    source.result = {"mko": []}
    await coordinator.async_fetch_now()
    assert coordinator.source_warned is True
    await unload(hass, entry)


async def test_stale_past_dates_never_move_last_real(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, hass_storage
) -> None:
    """A stale past-only list from the source never changes last_real."""
    freezer.move_to(at(12, 9))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator: BinDayCoordinator = entry.runtime_data
    assert coordinator.history["mko"].last_real == d(13)

    # Later knowledge says the last real date is 27.11.
    coordinator.history["mko"] = TypeHistory(last_real=d(27), ever_seen=True)

    freezer.move_to(at(14, 9))
    coordinator.recompute()
    assert coordinator.history["mko"].last_real == d(27)
    assert coordinator.data.resolved["mko"].date != d(13)

    # The source still returns the stale 13.11.
    await coordinator.async_fetch_now()
    assert coordinator.history["mko"].last_real == d(27)
    assert coordinator.data.resolved["mko"].date != d(13)
    stored = hass_storage[f"bin_day.{entry.entry_id}"]["data"]
    assert stored["types"]["mko"]["last_real"] == "2026-11-27"
    await unload(hass, entry)


async def test_fetch_slots(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(at(9, 5, 59, 59))
    entry = make_entry({CONF_REMINDERS_ENABLED: True, CONF_EVENING_TIME: "19:00"})
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    assert source.calls == 1  # at startup

    await tick(hass, freezer, at(9, 6))
    assert source.calls == 2
    await tick(hass, freezer, at(9, 7))  # 07:30 morning - 30 min
    assert source.calls == 3
    await tick(hass, freezer, at(9, 7, 30))  # 08:00 day-before - 30 min
    assert source.calls == 4
    await tick(hass, freezer, at(9, 12))
    assert source.calls == 5
    await tick(hass, freezer, at(9, 18, 30))  # fixed slot and 19:00 - 30 min
    assert source.calls == 6
    await tick(hass, freezer, at(9, 23, 59))
    assert source.calls == 6
    await unload(hass, entry)


async def test_no_reminder_slots_when_reminders_disabled(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 6, 30))
    entry = make_entry({CONF_REMINDERS_ENABLED: False, CONF_EVENING_TIME: "19:00"})
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    await tick(hass, freezer, at(9, 7))
    await tick(hass, freezer, at(9, 7, 30))
    assert source.calls == 1
    await unload(hass, entry)


async def test_midnight_refresh(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(at(12, 23, 59, 59))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    coordinator: BinDayCoordinator = entry.runtime_data
    assert coordinator.data.resolved["mko"].days == 1

    await tick(hass, freezer, at(13, 0, 0, 5))
    assert coordinator.data.resolved["mko"].days == 0
    assert coordinator.data.today == d(13)
    assert source.calls == 1
    await unload(hass, entry)


async def test_unload_removes_listeners(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 11, 50))
    entry = make_entry()
    source = FakeSource({"mko": [d(13)]})
    assert await setup(hass, entry, source)
    source.result = SourceError("down")
    await entry.runtime_data.async_fetch_now()  # leaves a pending retry
    await unload(hass, entry)
    calls = source.calls
    await tick(hass, freezer, at(9, 12, 30))
    await tick(hass, freezer, at(9, 18, 30))
    assert source.calls == calls
