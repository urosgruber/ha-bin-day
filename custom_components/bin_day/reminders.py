"""Reminders: send the due waste types to the chosen notify actions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import logging
from typing import Literal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceNotFound
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COLLECTION_TIME,
    CONF_DAY_BEFORE_TIME,
    CONF_EVENING_TIME,
    CONF_MISSING_WARNING,
    CONF_MORNING_TIME,
    CONF_NOTIFY_TARGETS,
)
from .coordinator import (
    DEFAULT_DAY_BEFORE_TIME,
    DEFAULT_EVENING_TIME,
    DEFAULT_MORNING_TIME,
    BinDayCoordinator,
    _parse_time,
    get_types,
)
from .schedule import due_types, is_done
from .texts import lang_for, missing_text, reminder_text

_LOGGER = logging.getLogger(__name__)

EVENING = "evening"
MORNING = "morning"
DAY_BEFORE = "day_before"

# Reminder key -> (option key, default time, switch default).
REMINDERS: dict[str, tuple[str, str, bool]] = {
    EVENING: (CONF_EVENING_TIME, DEFAULT_EVENING_TIME, True),
    MORNING: (CONF_MORNING_TIME, DEFAULT_MORNING_TIME, True),
    DAY_BEFORE: (CONF_DAY_BEFORE_TIME, DEFAULT_DAY_BEFORE_TIME, False),
}


def build_message(
    lang: str,
    kind: Literal["tomorrow", "today"],
    names: list[str],
    until: str | None,
) -> str:
    """Return the reminder text for the due names."""
    return reminder_text(lang, kind, names, until)


class ReminderScheduler:
    """Send the reminders and the missing-data warning at the set times."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, coordinator: BinDayCoordinator
    ) -> None:
        self.hass = hass
        self.entry = entry
        self.coordinator = coordinator
        # Off until the switch is added: a disabled switch entity never sends.
        # The switch sets its restored or default value when it is added.
        self.enabled: dict[str, bool] = dict.fromkeys(REMINDERS, False)
        self._unsubs: list[Callable[[], None]] = []

    @callback
    def async_start(self) -> None:
        """Track the reminder times from the options, in HA local time."""
        options = self.entry.options
        for key, (option, default, _on) in REMINDERS.items():
            moment = _parse_time(options.get(option, default))
            if moment is None:
                _LOGGER.warning("Invalid %s reminder time; the reminder is off", key)
                continue

            async def _fire(_now: datetime, key: str = key) -> None:
                await self._async_run(key)

            self._unsubs.append(
                async_track_time_change(
                    self.hass,
                    _fire,
                    hour=moment.hour,
                    minute=moment.minute,
                    second=moment.second,
                )
            )

    @callback
    def async_stop(self) -> None:
        """Remove all time listeners."""
        while self._unsubs:
            self._unsubs.pop()()

    async def _async_run(self, key: str) -> None:
        if self.enabled.get(key, False):
            await self._async_send_reminder(key)
        if key == EVENING and self.entry.options.get(CONF_MISSING_WARNING, True):
            await self._async_send_missing_warning()

    async def _async_send_reminder(self, key: str) -> None:
        data = self.coordinator.data
        if data is None:
            return
        now = dt_util.now()
        today = now.date()
        collection = _parse_time(self.entry.options.get(CONF_COLLECTION_TIME))
        kind: Literal["tomorrow", "today"]
        if key == MORNING:
            if is_done(now, today, collection):
                return
            kind, day = "today", today
        else:
            kind, day = "tomorrow", today + timedelta(days=1)
        names = self._due_names(day)
        if not names:
            return
        until = collection.strftime("%H:%M") if kind == "today" and collection else None
        await self._async_notify(build_message(lang_for(self.hass, self.entry), kind, names, until))

    def _due_names(self, day: date) -> list[str]:
        resolved = self.coordinator.data.resolved
        configs = get_types(self.entry)
        names: list[str] = []
        for key in due_types(resolved, day):
            res = resolved[key]
            if res.status not in ("real", "estimate") or key not in configs:
                continue
            names.append(configs[key]["name"] + ("*" if res.estimate else ""))
        return names

    async def _async_send_missing_warning(self) -> None:
        """Warn once per gap per type, and once while the source is down.

        The flags are set and saved only after a target received the warning,
        so a failed send is tried again the next evening.
        """
        coordinator = self.coordinator
        data = coordinator.data
        if data is None:
            return
        missing = [
            key
            for key, res in data.resolved.items()
            if res.status == "no_data"
            and (hist := coordinator.history.get(key)) is not None
            and not hist.warned
        ]
        source_down = (
            not data.source_ok
            and all(res.date is None for res in data.resolved.values())
            and not coordinator.source_warned
        )
        if not missing and not source_down:
            return
        # One message covers all types: the text names no type.
        if not await self._async_notify(missing_text(lang_for(self.hass, self.entry))):
            return
        for key in missing:
            coordinator.history[key].warned = True
        if source_down:
            coordinator.source_warned = True
        await coordinator.async_save()

    async def _async_notify(self, message: str) -> int:
        """Send to every target; a failing target never blocks the others.

        Return the number of targets that received the message.
        """
        payload = {"title": self.entry.title, "message": message}
        sent = 0
        for target in self.entry.options.get(CONF_NOTIFY_TARGETS) or []:
            try:
                await self.hass.services.async_call(
                    "notify", target, dict(payload), blocking=True
                )
            except ServiceNotFound:
                _LOGGER.warning("Notify action notify.%s does not exist", target)
            except HomeAssistantError as err:
                _LOGGER.warning("Notify action notify.%s failed: %s", target, err)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Notify action notify.%s failed unexpectedly: %s", target, err
                )
            else:
                sent += 1
        return sent
