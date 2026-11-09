"""Base entity of Bin Day."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BinDayCoordinator


class BinDayEntity(CoordinatorEntity[BinDayCoordinator]):
    """An entity that belongs to the Bin Day device of one entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: BinDayCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Simbio",
            model="Bin Day",
        )
