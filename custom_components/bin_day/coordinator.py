"""Coordinator: fetch dates, learn, persist, fetch times, retries and repairs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDRESS_NAME,
    CONF_CITY,
    CONF_COLLECTION_TIME,
    CONF_DAY_BEFORE_TIME,
    CONF_EVENING_TIME,
    CONF_MORNING_TIME,
    CONF_REMINDERS_ENABLED,
    CONF_TYPES,
    DOMAIN,
    FETCH_TIMES,
    PRE_REMINDER_FETCH,
    RETRY_DELAY,
    RETRY_MAX,
    TYPE_DEFAULTS,
)
from .schedule import Resolved, TypeHistory, done_at, learn, resolve
from .source import AddressNotFound, Source, SourceError

if TYPE_CHECKING:
    from .reminders import ReminderScheduler

_LOGGER = logging.getLogger(__name__)

STORE_VERSION = 1
MIDNIGHT_RECOMPUTE = time(0, 0, 5)

# Option defaults for the reminder times (Task 8 owns the reminders).
DEFAULT_EVENING_TIME = "19:00"
DEFAULT_MORNING_TIME = "07:30"
DEFAULT_DAY_BEFORE_TIME = "08:00"


@dataclass
class BinDayData:
    """The resolved state of all configured types."""

    resolved: dict[str, Resolved]
    today: date
    source_ok: bool
    last_success: datetime | None


def get_types(entry: ConfigEntry) -> dict[str, dict[str, Any]]:
    """Return type key -> {name, icon, color, estimate, interval}, in config order.

    The entry data holds the setup values. The options hold per-field
    overrides, and they win. This is the only place that reads the types.
    """
    overrides = entry.options.get(CONF_TYPES) or {}
    types: dict[str, dict[str, Any]] = {}
    for key, conf in (entry.data.get(CONF_TYPES) or {}).items():
        types[key] = {**conf, **(overrides.get(key) or {})}
    return types


def ordered_keys(types: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Return the type keys in the order to show them to the user.

    A type with an `order` number goes by it. Else a known type goes by its
    place in the Simbio defaults. Other types follow in their dict order.
    """
    known = list(TYPE_DEFAULTS)

    def rank(item: tuple[int, tuple[str, Mapping[str, Any]]]) -> tuple[float, int]:
        position, (key, conf) = item
        order = conf.get("order")
        if isinstance(order, (int, float)) and not isinstance(order, bool):
            return (order, position)
        if key in known:
            return (known.index(key), position)
        return (len(known) + position, position)

    return [key for _pos, (key, _conf) in sorted(enumerate(types.items()), key=rank)]


def _parse_time(value: Any) -> time | None:
    if isinstance(value, time):
        return value
    if isinstance(value, str) and value:
        return dt_util.parse_time(value)
    return None


def _load_dates(key: str, raw: Any) -> list[date]:
    """Parse stored ISO dates; skip corrupt values with a warning."""
    if not isinstance(raw, list):
        _LOGGER.warning("Stored real dates of type %s are corrupt; ignoring them", key)
        return []
    dates: list[date] = []
    for value in raw:
        try:
            if not isinstance(value, str):
                raise TypeError("not a string")
            dates.append(date.fromisoformat(value))
        except (TypeError, ValueError) as err:
            _LOGGER.warning(
                "Stored real date %r of type %s is corrupt (%s); skipping it",
                value,
                key,
                err,
            )
    return dates


def _minus(value: time, delta: timedelta) -> time:
    moment = datetime.combine(date(2000, 1, 2), value) - delta
    return moment.time()


class BinDayCoordinator(DataUpdateCoordinator[BinDayData]):
    """Fetch collection dates at fixed times and keep the learned history."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, source: Source) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=None,
        )
        self._source = source
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self.history: dict[str, TypeHistory] = {}
        self.source_warned = False
        # Set by the entry setup: the reminder scheduler (None when reminders
        # are off) and the platforms that were forwarded.
        self.reminders: ReminderScheduler | None = None
        self.platforms: list[Platform] = []
        self._last_real_dates: dict[str, list[date]] = {}
        self._last_success: datetime | None = None
        self._retries = 0
        self._cancel_retry: Callable[[], None] | None = None
        self._issue_id = f"address_not_found_{entry.entry_id}"

    @property
    def type_keys(self) -> list[str]:
        """Return the configured type keys in config order."""
        return list(get_types(self.config_entry))

    # ----- store -----

    async def _async_setup(self) -> None:
        """Load the stored history before the first fetch."""
        stored = await self._store.async_load()
        if not isinstance(stored, dict):
            return
        types = stored.get("types")
        if isinstance(types, dict):
            for key, raw in types.items():
                try:
                    if not isinstance(raw, dict):
                        raise TypeError("not a mapping")
                    self.history[key] = TypeHistory.from_dict(raw)
                except (TypeError, ValueError) as err:
                    _LOGGER.warning(
                        "Stored history of type %s is corrupt (%s); starting fresh",
                        key,
                        err,
                    )
                    self.history[key] = TypeHistory()
        self.source_warned = bool(stored.get("source_warned", False))
        real = stored.get("real")
        if isinstance(real, dict):
            for key, raw in real.items():
                self._last_real_dates[key] = _load_dates(key, raw)

    async def async_save(self) -> None:
        """Persist the history, the last real dates and the warning flag."""
        await self._store.async_save(
            {
                "types": {key: hist.to_dict() for key, hist in self.history.items()},
                "real": {
                    key: [value.isoformat() for value in dates]
                    for key, dates in self._last_real_dates.items()
                },
                "source_warned": self.source_warned,
            }
        )

    # ----- fetching -----

    async def async_fetch_now(self) -> None:
        """Start a new fetch slot and fetch now."""
        self._cancel_pending_retry()
        self._retries = 0
        await self.async_refresh()

    async def _async_retry(self, _now: datetime) -> None:
        self._cancel_retry = None
        await self.async_refresh()

    @callback
    def _cancel_pending_retry(self) -> None:
        if self._cancel_retry is not None:
            self._cancel_retry()
            self._cancel_retry = None

    async def _async_update_data(self) -> BinDayData:
        today = dt_util.now().date()
        try:
            result = await self._source.async_fetch()
        except AddressNotFound as err:
            self._create_issue()
            return self._failed(err, retry=False)
        except SourceError as err:
            return self._failed(err, retry=True)

        self._last_real_dates = {key: list(value) for key, value in result.items()}
        for key in self.type_keys:
            real = [d for d in result.get(key, []) if d >= today]
            self.history[key] = learn(self.history.get(key, TypeHistory()), real)
        if any(d >= today for dates in result.values() for d in dates):
            # The source works again: re-arm the source-down warning.
            self.source_warned = False
        await self.async_save()
        self._last_success = dt_util.utcnow()
        ir.async_delete_issue(self.hass, DOMAIN, self._issue_id)
        return self._build(today, source_ok=True)

    def _failed(self, err: SourceError, *, retry: bool) -> BinDayData:
        if self.data is None and not any(h.ever_seen for h in self.history.values()):
            raise UpdateFailed(f"Bin Day source failed and no history exists: {err}")
        _LOGGER.warning("Bin Day source failed: %s", err)
        if retry and self._retries < RETRY_MAX:
            self._retries += 1
            self._cancel_pending_retry()
            self._cancel_retry = async_call_later(self.hass, RETRY_DELAY, self._async_retry)
        return self._build(dt_util.now().date(), source_ok=False)

    def _create_issue(self) -> None:
        data = self.config_entry.data
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="address_not_found",
            translation_placeholders={
                "address": f"{data.get(CONF_ADDRESS_NAME, '')}, {data.get(CONF_CITY, '')}"
            },
        )

    # ----- resolving -----

    def _build(self, today: date, *, source_ok: bool) -> BinDayData:
        resolved: dict[str, Resolved] = {}
        for key, conf in get_types(self.config_entry).items():
            real = [d for d in self._last_real_dates.get(key, []) if d >= today]
            resolved[key] = resolve(
                self.history.get(key, TypeHistory()),
                real,
                today,
                estimate_enabled=conf.get("estimate", True),
                interval_override=conf.get("interval"),
            )
        return BinDayData(
            resolved=resolved,
            today=today,
            source_ok=source_ok,
            last_success=self._last_success,
        )

    @callback
    def recompute(self) -> None:
        """Resolve again with the current date, without a network call."""
        source_ok = self.data.source_ok if self.data is not None else True
        self.async_set_updated_data(self._build(dt_util.now().date(), source_ok=source_ok))

    # ----- timers -----

    def _fetch_times(self) -> list[time]:
        times: set[time] = set(FETCH_TIMES)
        options = self.config_entry.options
        if options.get(CONF_REMINDERS_ENABLED):
            for key, default in (
                (CONF_EVENING_TIME, DEFAULT_EVENING_TIME),
                (CONF_MORNING_TIME, DEFAULT_MORNING_TIME),
                (CONF_DAY_BEFORE_TIME, DEFAULT_DAY_BEFORE_TIME),
            ):
                value = _parse_time(options.get(key, default))
                if value is not None:
                    times.add(_minus(value, PRE_REMINDER_FETCH))
        return sorted(times)

    @callback
    def async_start_timers(self) -> None:
        """Track fetch slots and recompute times; removed on unload."""
        entry = self.config_entry
        entry.async_on_unload(self._cancel_pending_retry)

        async def _on_slot(_now: datetime) -> None:
            await self.async_fetch_now()

        @callback
        def _on_recompute(_now: datetime) -> None:
            self.recompute()

        for slot in self._fetch_times():
            entry.async_on_unload(
                async_track_time_change(
                    self.hass, _on_slot, hour=slot.hour, minute=slot.minute, second=slot.second
                )
            )
        recompute_times = {
            MIDNIGHT_RECOMPUTE,
            done_at(_parse_time(entry.options.get(CONF_COLLECTION_TIME))),
        }
        for moment in recompute_times:
            entry.async_on_unload(
                async_track_time_change(
                    self.hass,
                    _on_recompute,
                    hour=moment.hour,
                    minute=moment.minute,
                    second=moment.second,
                )
            )
