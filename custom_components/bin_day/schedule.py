"""Pure schedule logic: learn dates, estimate the next date, decide when done."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Literal

from .const import (
    DEFAULT_DONE,
    DEFAULT_INTERVAL,
    INTERVAL_MAX,
    INTERVAL_MIN,
    TRUST_INTERVALS,
)


@dataclass
class TypeHistory:
    """What the integration has learned about one waste type."""

    last_real: date | None = None
    interval: int = DEFAULT_INTERVAL
    ever_seen: bool = False
    warned: bool = False

    def to_dict(self) -> dict:
        return {
            "last_real": self.last_real.isoformat() if self.last_real else None,
            "interval": self.interval,
            "ever_seen": self.ever_seen,
            "warned": self.warned,
        }

    @classmethod
    def from_dict(cls, data: dict) -> TypeHistory:
        last = data.get("last_real")
        return cls(
            last_real=date.fromisoformat(last) if last else None,
            interval=data.get("interval", DEFAULT_INTERVAL),
            ever_seen=data.get("ever_seen", False),
            warned=data.get("warned", False),
        )


@dataclass(frozen=True)
class Resolved:
    """The next collection of one type."""

    date: date | None
    estimate: bool
    status: Literal["real", "estimate", "no_data", "never"]
    dates: tuple[date, ...]
    days: int | None


def _valid_gap(gap: int) -> bool:
    return INTERVAL_MIN <= gap <= INTERVAL_MAX


def learn(hist: TypeHistory, real: list[date]) -> TypeHistory:
    """Return a new history that includes the real dates. Never mutates."""
    dates = sorted(set(real))
    if not dates:
        return replace(hist)
    interval = hist.interval
    last_real = hist.last_real
    for a, b in zip(dates, dates[1:]):
        gap = (b - a).days
        if _valid_gap(gap):
            interval = gap
    newest = dates[-1]
    if last_real is None:
        last_real = newest
    elif newest > last_real:
        gap = (dates[0] - last_real).days
        if _valid_gap(gap):
            interval = gap
        last_real = newest
    elif newest < last_real and last_real >= dates[0]:
        last_real = newest
    return TypeHistory(
        last_real=last_real, interval=interval, ever_seen=True, warned=False
    )


def resolve(
    hist: TypeHistory,
    real: list[date],
    today: date,
    *,
    estimate_enabled: bool,
    interval_override: int | None,
) -> Resolved:
    """Find the next collection: a real date, an estimate, or nothing."""
    step = interval_override or hist.interval
    future = sorted(d for d in real if d >= today)
    if future:
        return Resolved(future[0], False, "real", tuple(future), (future[0] - today).days)
    if not hist.ever_seen or hist.last_real is None:
        return Resolved(None, False, "never", (), None)
    no_data = Resolved(None, False, "no_data", (), None)
    if not estimate_enabled:
        return no_data
    n = max(0, ceil((today - hist.last_real).days / step))
    est = hist.last_real + timedelta(days=n * step)
    while est < today:
        est += timedelta(days=step)
    if (est - hist.last_real).days > TRUST_INTERVALS * step:
        return no_data
    return Resolved(est, True, "estimate", (est,), (est - today).days)


def done_at(collection_time: time | None) -> time:
    """Time when a collection counts as done: collection time + 1 h, max 23:59."""
    if collection_time is None:
        return DEFAULT_DONE
    hour = collection_time.hour + 1
    if hour > 23:
        return time(23, 59)
    return collection_time.replace(hour=hour)


def is_done(now: datetime, day: date, collection_time: time | None) -> bool:
    """Return True when the collection on this day is over."""
    return day == now.date() and now.time() >= done_at(collection_time)


def due_types(resolved: dict[str, Resolved], day: date) -> list[str]:
    """Return the type keys that have a collection on this day, in config order."""
    return [key for key, res in resolved.items() if res.date == day]
