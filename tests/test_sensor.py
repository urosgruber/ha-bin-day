"""Tests for the collection and next-collection sensors."""

from __future__ import annotations

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.bin_day.const import CONF_COLLECTION_TIME

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


def next_entity(hass: HomeAssistant, entry) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", "bin_day", f"{entry.entry_id}_next")
    assert entity_id is not None
    return entity_id


async def test_type_sensor_real(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    state = hass.states.get("sensor.ljubljanska_cesta_1_a_mixed_waste")
    assert state is not None
    assert state.state == "2026-11-13"
    assert state.attributes["days"] == 4
    assert state.attributes["estimate"] is False
    assert state.attributes["color"] == "#00a136"
    assert state.attributes["type_key"] == "mko"
    assert state.attributes["type_name"] == "Mixed waste"
    assert state.attributes["order"] == 0
    assert state.attributes["dates"] == ["2026-11-13"]
    assert state.attributes["last_real"] == "2026-11-13"
    await unload(hass, entry)


async def test_type_sensor_estimate_attrs(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    source = FakeSource({"mko": [d(13), d(27)]})
    assert await setup(hass, entry, source)
    source.result = {"mko": []}
    freezer.move_to(at(28, 9))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    state = hass.states.get("sensor.ljubljanska_cesta_1_a_mixed_waste")
    assert state.attributes["estimate"] is True
    assert state.attributes["status"] == "estimate"
    assert state.state == "2026-12-11"
    await unload(hass, entry)


async def test_type_sensor_never_unknown(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "bio": []}))
    state = hass.states.get("sensor.ljubljanska_cesta_1_a_bio_waste")
    assert state.state == "unknown"
    assert state.attributes["status"] == "never"
    await unload(hass, entry)


async def test_next_tomorrow_sl(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.config.language = "sl"
    freezer.move_to(at(12, 19))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    state = hass.states.get(next_entity(hass, entry))
    assert state.state == "Jutri: Mixed waste"
    assert state.attributes["date"] == "2026-11-13"
    assert state.attributes["types"] == ["mko"]
    assert state.attributes["today"] == "2026-11-12"
    assert state.attributes["entry_id"] == entry.entry_id
    await unload(hass, entry)


async def test_next_today_until_done(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(13, 7, 30))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    entity_id = next_entity(hass, entry)
    assert entity_id == "sensor.ljubljanska_cesta_1_a_next_collection"
    state = hass.states.get(entity_id)
    assert state.state == "Today: Mixed waste"
    assert state.attributes["done"] is False

    await tick(hass, freezer, at(13, 12, 0, 5))
    state = hass.states.get(entity_id)
    assert state.state == "none"
    assert state.attributes["done"] is True
    await unload(hass, entry)


async def test_next_today_wins_then_tomorrow(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to(at(13, 7, 30))
    entry = make_entry({CONF_COLLECTION_TIME: "08:00"})
    assert await setup(hass, entry, FakeSource({"mko": [d(13)], "emb": [d(14)]}))
    entity_id = next_entity(hass, entry)
    state = hass.states.get(entity_id)
    assert state.state == "Today: Mixed waste"
    assert state.attributes["until"] == "08:00"
    await tick(hass, freezer, at(13, 9, 0, 5))
    assert hass.states.get(entity_id).state == "Tomorrow: Packaging"
    await unload(hass, entry)


async def test_next_estimate_star(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    source = FakeSource({"emb": [d(13)]})
    assert await setup(hass, entry, source)
    source.result = {"emb": []}
    freezer.move_to(at(26, 9))
    await entry.runtime_data.async_fetch_now()
    await hass.async_block_till_done()
    assert hass.states.get(next_entity(hass, entry)).state == "Tomorrow: Packaging*"
    await unload(hass, entry)


async def test_unique_ids_stable(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.move_to(at(9, 9))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", "bin_day", f"{entry.entry_id}_mko")
    assert registry.async_get_entity_id("sensor", "bin_day", f"{entry.entry_id}_next")
    await unload(hass, entry)


async def test_next_today_sl(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    hass.config.language = "sl"
    freezer.move_to(at(13, 7, 30))
    entry = make_entry()
    assert await setup(hass, entry, FakeSource({"mko": [d(13)]}))
    assert hass.states.get(next_entity(hass, entry)).state == "Danes: Mixed waste"
    await unload(hass, entry)
