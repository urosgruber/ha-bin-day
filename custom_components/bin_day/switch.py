"""Reminder switches: one per reminder time."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_INITIAL_SWITCH_STATES, DOMAIN
from .coordinator import BinDayCoordinator
from .reminders import REMINDERS, ReminderScheduler


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the reminder switches of an entry."""
    coordinator: BinDayCoordinator = entry.runtime_data
    scheduler = coordinator.reminders
    if scheduler is None:
        return
    wanted = entry.options.get(CONF_INITIAL_SWITCH_STATES) or {}
    async_add_entities(
        BinDayReminderSwitch(entry, scheduler, key, bool(wanted.get(key, default)))
        for key, (_option, _time, default) in REMINDERS.items()
    )


class BinDayReminderSwitch(RestoreEntity, SwitchEntity):
    """Turn one reminder on or off. The state survives a restart."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(
        self, entry: ConfigEntry, scheduler: ReminderScheduler, key: str, default: bool
    ) -> None:
        self._scheduler = scheduler
        self._key = key
        self._attr_translation_key = f"reminder_{key}"
        self._attr_unique_id = f"{entry.entry_id}_reminder_{key}"
        self._attr_is_on = default
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Simbio",
            model="Bin Day",
        )

    async def async_added_to_hass(self) -> None:
        """Restore the last state, else keep the initial state."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._attr_is_on = last.state == STATE_ON
        self._scheduler.enabled[self._key] = bool(self._attr_is_on)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._set(False)

    def _set(self, on: bool) -> None:
        self._attr_is_on = on
        self._scheduler.enabled[self._key] = on
        self.async_write_ha_state()
