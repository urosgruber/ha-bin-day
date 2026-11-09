"""Sensors: one per waste type, and the next collection."""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_COLLECTION_TIME
from .coordinator import BinDayCoordinator, _parse_time, get_type_configs
from .entity import BinDayEntity
from .schedule import Resolved, done_at, due_types, is_done
from .texts import NONE_STATE, lang_for, next_state


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of an entry."""
    coordinator: BinDayCoordinator = entry.runtime_data
    entities: list[BinDayEntity] = [
        BinDayTypeSensor(coordinator, entry, key, index)
        for index, key in enumerate(get_type_configs(entry))
    ]
    entities.append(BinDayNextSensor(coordinator, entry))
    async_add_entities(entities)


class BinDayTypeSensor(BinDayEntity, SensorEntity):
    """The next collection date of one waste type."""

    # The entity name is the configured type name, so it has no translated name.
    _attr_translation_key = "collection"
    _attr_device_class = SensorDeviceClass.DATE

    def __init__(
        self, coordinator: BinDayCoordinator, entry: ConfigEntry, key: str, index: int
    ) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._index = index
        conf = get_type_configs(entry)[key]
        self._conf = conf
        self._attr_name = conf["name"]
        self._attr_icon = conf.get("icon")
        self._attr_unique_id = f"{entry.entry_id}_{key}"

    @property
    def _resolved(self) -> Resolved | None:
        return self.coordinator.data.resolved.get(self._key)

    @property
    def native_value(self) -> date | None:
        res = self._resolved
        return res.date if res else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        res = self._resolved
        hist = self.coordinator.history.get(self._key)
        return {
            "type_key": self._key,
            "type_name": self._conf["name"],
            "order": self._index,
            "estimate": res.estimate if res else False,
            "status": res.status if res else "never",
            "days": res.days if res else None,
            "dates": [d.isoformat() for d in res.dates] if res else [],
            "color": self._conf.get("color"),
            "icon": self._conf.get("icon"),
            "last_real": hist.last_real.isoformat() if hist and hist.last_real else None,
            "interval": self._conf.get("interval") or (hist.interval if hist else None),
        }


class BinDayNextSensor(BinDayEntity, SensorEntity):
    """What is due today or tomorrow, as a short text."""

    _attr_translation_key = "next"

    def __init__(self, coordinator: BinDayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_next"

    def _collection_time(self) -> time | None:
        return _parse_time(self._entry.options.get(CONF_COLLECTION_TIME))

    def _compute(self) -> tuple[str, list[str], date | None, bool]:
        """Return kind, due keys, due date and the done flag."""
        data = self.coordinator.data
        now = dt_util.now()
        today = data.today
        collection = self._collection_time()
        today_keys = due_types(data.resolved, today)
        done = bool(today_keys) and is_done(now, today, collection)
        if today_keys and not done:
            return "today", today_keys, today, False
        tomorrow = today + timedelta(days=1)
        tomorrow_keys = due_types(data.resolved, tomorrow)
        if tomorrow_keys:
            return "tomorrow", tomorrow_keys, tomorrow, done
        return "none", [], None, done

    @property
    def native_value(self) -> str:
        kind, keys, _day, _done = self._compute()
        if kind == "none":
            return NONE_STATE
        configs = get_type_configs(self._entry)
        resolved = self.coordinator.data.resolved
        names = [
            configs[key]["name"] + ("*" if resolved[key].estimate else "") for key in keys
        ]
        return next_state(lang_for(self.hass), kind, names)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        _kind, keys, day, done = self._compute()
        collection = self._collection_time()
        until = done_at(collection)
        return {
            "today": self.coordinator.data.today.isoformat(),
            "types": keys,
            "date": day.isoformat() if day else None,
            "days": (day - self.coordinator.data.today).days if day else None,
            "done": done,
            "until": until.strftime("%H:%M") if collection else None,
            "entry_id": self._entry.entry_id,
        }
