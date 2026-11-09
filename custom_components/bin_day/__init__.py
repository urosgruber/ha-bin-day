"""The Bin Day integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from .const import (
    CONF_ADDRESS_ID,
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_REMINDERS_ENABLED,
    DOMAIN,
)
from .coordinator import STORE_VERSION, BinDayCoordinator
from .reminders import ReminderScheduler
from .simbio import SimbioAddress, SimbioClient, SimbioSource
from .source import Source

PLATFORMS: list[Platform] = [Platform.SENSOR]
# Forwarded only when reminders are enabled.
REMINDER_PLATFORMS: list[Platform] = [Platform.SWITCH]

type BinDayConfigEntry = ConfigEntry[BinDayCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_URL = "/bin_day/bin-day-card.js"
CARD_PATH = Path(__file__).parent / "frontend" / "bin-day-card.js"
_CARD_REGISTERED = f"{DOMAIN}_card_registered"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the card and load it in the frontend, once per HA start."""
    if hass.data.get(_CARD_REGISTERED):
        return True
    hass.data[_CARD_REGISTERED] = True
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), True)]
    )
    integration = await async_get_integration(hass, DOMAIN)
    frontend.add_extra_js_url(hass, f"{CARD_URL}?v={integration.version}")
    return True


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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the repair issue and the stored history of a removed entry."""
    ir.async_delete_issue(hass, DOMAIN, f"address_not_found_{entry.entry_id}")
    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()
