"""Tests for the pure schedule logic."""

from datetime import date, datetime, time

from custom_components.bin_day.schedule import (
    Resolved,
    TypeHistory,
    done_at,
    due_types,
    is_done,
    learn,
    resolve,
)


def d(day: int, month: int, year: int = 2026) -> date:
    return date(year, month, day)


def test_learn_first_date():
    h = learn(TypeHistory(), [d(13, 11)])
    assert h.last_real == d(13, 11)
    assert h.interval == 14
    assert h.ever_seen is True


def test_learn_gap_sets_interval():
    base = TypeHistory(last_real=d(13, 11), ever_seen=True)
    h = learn(base, [d(27, 11)])
    assert h.interval == 14
    assert h.last_real == d(27, 11)
    h = learn(base, [d(11, 12)])
    assert h.interval == 28


def test_learn_ignores_long_gap():
    base = TypeHistory(last_real=d(13, 11), ever_seen=True)
    h = learn(base, [d(1, 1, 2027)])
    assert h.interval == 14
    assert h.last_real == d(1, 1, 2027)


def test_learn_from_future_list():
    h = learn(TypeHistory(), [d(2, 12), d(9, 12), d(16, 12)])
    assert h.interval == 7


def test_learn_correction_moves_last_real_back():
    base = TypeHistory(last_real=d(20, 11), ever_seen=True)
    h = learn(base, [d(18, 11)])
    assert h.last_real == d(18, 11)


def test_learn_empty_keeps_history():
    base = TypeHistory(last_real=d(20, 11), interval=21, ever_seen=True, warned=True)
    h = learn(base, [])
    assert h == base
    assert h.warned is True


def test_resolve_real():
    h = TypeHistory(last_real=d(13, 11), ever_seen=True)
    r = resolve(h, [d(13, 11)], d(9, 11), estimate_enabled=True, interval_override=None)
    assert r.status == "real"
    assert r.days == 4
    assert r.dates == (d(13, 11),)
    assert r.estimate is False


def test_resolve_estimate():
    h = TypeHistory(last_real=d(13, 11), ever_seen=True)
    r = resolve(h, [], d(16, 11), estimate_enabled=True, interval_override=None)
    assert r.date == d(27, 11)
    assert r.estimate is True
    assert r.status == "estimate"
    assert r.days == 11


def test_resolve_estimate_today():
    h = TypeHistory(last_real=d(13, 11), ever_seen=True)
    r = resolve(h, [], d(27, 11), estimate_enabled=True, interval_override=None)
    assert r.date == d(27, 11)
    assert r.days == 0


def test_resolve_trust_limit():
    h = TypeHistory(last_real=d(13, 11), ever_seen=True)
    r = resolve(h, [], d(12, 12), estimate_enabled=True, interval_override=None)
    assert r.status == "no_data"
    assert r.date is None
    r = resolve(h, [], d(10, 12), estimate_enabled=True, interval_override=None)
    assert r.status == "estimate"
    assert r.date == d(11, 12)


def test_resolve_never():
    r = resolve(TypeHistory(), [], d(9, 11), estimate_enabled=True, interval_override=None)
    assert r.status == "never"
    assert r == Resolved(None, False, "never", (), None)


def test_resolve_estimate_disabled():
    h = TypeHistory(last_real=d(13, 11), ever_seen=True)
    r = resolve(h, [], d(16, 11), estimate_enabled=False, interval_override=None)
    assert r == Resolved(None, False, "no_data", (), None)


def test_resolve_override_interval():
    h = TypeHistory(last_real=d(13, 11), ever_seen=True)
    r = resolve(h, [], d(16, 11), estimate_enabled=True, interval_override=7)
    assert r.date == d(20, 11)


def test_done_at():
    assert done_at(None) == time(12, 0)
    assert done_at(time(11, 0)) == time(12, 0)
    assert done_at(time(23, 30)) == time(23, 59)


def test_is_done():
    day = d(13, 11)
    assert not is_done(datetime(2026, 11, 13, 11, 59), day, None)
    assert is_done(datetime(2026, 11, 13, 12, 0), day, None)
    assert not is_done(datetime(2026, 11, 14, 12, 0), day, None)


def test_due_types_order():
    day = d(13, 11)
    mk = lambda dt: Resolved(dt, False, "real", (dt,), 0)  # noqa: E731
    res = {"emb": mk(day), "mko": mk(day), "bio": mk(d(20, 11))}
    assert due_types(res, day) == ["emb", "mko"]


def test_history_roundtrip():
    h = TypeHistory(last_real=d(13, 11), interval=21, ever_seen=True, warned=True)
    assert TypeHistory.from_dict(h.to_dict()) == h
    assert TypeHistory.from_dict(TypeHistory().to_dict()) == TypeHistory()
    assert TypeHistory.from_dict({}) == TypeHistory()
