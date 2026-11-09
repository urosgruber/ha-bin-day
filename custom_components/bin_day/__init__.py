"""The Bin Day integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_ADDRESS_ID, CONF_ADDRESS_NAME, CONF_CITY, CONF_REMINDERS_ENABLED
from .coordinator import BinDayCoordinator
from .reminders import ReminderScheduler
from .simbio import SimbioAddress, SimbioClient, SimbioSource
from .source import Source

PLATFORMS: list[Platform] = [Platform.SENSOR]
# Forwarded only when reminders are enabled.
REMINDER_PLATFORMS: list[Platform] = [Platform.SWITCH]

type BinDayConfigEntry = ConfigEntry[BinDayCoordinator]


def _create_source(hass: HomeAssistant, entry: ConfigEntry) -> Source:
    """Build the Simbio source for the stored address."""
    client = SimbioClient(async_get_clientsession(hass))
    address = SimbioAddress(
        id=str(entry.data[CONF_ADDRESS_ID]),
        name=entry.data[CONF_ADDRESS_NAME],
        city=entry.data[CONF_CITY],
    )
    return SimbioSource(client, address)


async def async_setup_entry(hass: HomeAssistant, entry: BinDayConfigEntry) -> bool:
    """Set up Bin Day from a config entry."""
    coordinator = BinDayCoordinator(hass, entry, _create_source(hass, entry))
    # Setup fails (and HA retries it) only when the source fails and no
    # stored history exists.
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    coordinator.async_start_timers()
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    platforms = list(PLATFORMS)
    if entry.options.get(CONF_REMINDERS_ENABLED, False):
        scheduler = ReminderScheduler(hass, entry, coordinator)
        coordinator.reminders = scheduler
        scheduler.async_start()
        entry.async_on_unload(scheduler.async_stop)
        platforms += REMINDER_PLATFORMS
    # Unload reads this list: the options may change before the unload.
    coordinator.platforms = platforms
    await hass.config_entries.async_forward_entry_setups(entry, platforms)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: BinDayConfigEntry) -> None:
    """Reload the entry when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BinDayConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry, entry.runtime_data.platforms
    )
