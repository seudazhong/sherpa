"""Cadence engine unit tests (ADR-031, Phase CRON) — pure, no DB.

Covers every cadence_kind's next-occurrence, DST-correctness (wall-clock preserved
across a spring-forward boundary), the interval min-frequency floor, monthly short-
month skipping, and validation errors.
"""

from __future__ import annotations

import datetime

import pytest

from app.scheduler import cadence

UTC = datetime.UTC


def _dt(y, m, d, hh=0, mm=0, tz=UTC):  # type: ignore[no-untyped-def]
    return datetime.datetime(y, m, d, hh, mm, tzinfo=tz)


def test_once_is_terminal() -> None:
    nxt = cadence.compute_next(
        cadence_kind="once", after=_dt(2026, 7, 23, 10), anchor=_dt(2026, 7, 23, 9), timezone="UTC"
    )
    assert nxt is None


def test_interval_steps_from_anchor_strictly_after() -> None:
    anchor = _dt(2026, 7, 23, 9, 0)
    after = _dt(2026, 7, 23, 9, 5)
    nxt = cadence.compute_next(
        cadence_kind="interval", after=after, anchor=anchor, timezone="UTC", interval_seconds=120
    )
    assert nxt == _dt(2026, 7, 23, 9, 6)  # 9:00 + 3*120s = 9:06, first grid point > 9:05


def test_interval_below_floor_raises() -> None:
    with pytest.raises(cadence.CadenceError):
        cadence.compute_next(
            cadence_kind="interval",
            after=_dt(2026, 7, 23, 9),
            anchor=_dt(2026, 7, 23, 9),
            timezone="UTC",
            interval_seconds=30,
        )


def test_cron_weekday_nine_am_in_tz() -> None:
    # Sat 2026-07-25 -> next weekday 09:00 Asia/Shanghai is Mon 2026-07-27 09:00 (+08:00).
    after = _dt(2026, 7, 25, 12, tz=UTC)
    nxt = cadence.compute_next(
        cadence_kind="cron",
        after=after,
        anchor=after,
        timezone="Asia/Shanghai",
        cron_expr="0 9 * * 1-5",
    )
    assert nxt is not None
    sh = nxt.astimezone(cadence._tz("Asia/Shanghai"))
    assert (sh.year, sh.month, sh.day, sh.hour, sh.weekday()) == (2026, 7, 27, 9, 0)
    assert nxt > after


def test_daily_next_local_time() -> None:
    tz = "Asia/Shanghai"
    after = _dt(2026, 7, 23, 3, tz=UTC)  # 11:00 SH
    nxt = cadence.compute_next(
        cadence_kind="daily",
        after=after,
        anchor=after,
        timezone=tz,
        local_time=datetime.time(8, 0),
    )
    assert nxt is not None
    sh = nxt.astimezone(cadence._tz(tz))
    assert (sh.hour, sh.minute) == (8, 0)
    assert sh.date() == datetime.date(2026, 7, 24)  # 08:00 already passed today → tomorrow


def test_weekly_selected_days() -> None:
    tz = "UTC"
    # Thu 2026-07-23. weekly_days = Mon(0),Wed(2) at 09:00 → next is Mon 2026-07-27 09:00.
    after = _dt(2026, 7, 23, 10)
    nxt = cadence.compute_next(
        cadence_kind="weekly",
        after=after,
        anchor=after,
        timezone=tz,
        weekly_days="0,2",
        local_time=datetime.time(9, 0),
    )
    assert nxt == _dt(2026, 7, 27, 9, 0)
    assert nxt.weekday() == 0


def test_monthly_skips_short_months() -> None:
    tz = "UTC"
    # day 31 at 09:00 starting 2026-02-15 → Feb has no 31st, next is 2026-03-31.
    after = _dt(2026, 2, 15, 10)
    nxt = cadence.compute_next(
        cadence_kind="monthly",
        after=after,
        anchor=after,
        timezone=tz,
        monthly_day=31,
        local_time=datetime.time(9, 0),
    )
    assert nxt == _dt(2026, 3, 31, 9, 0)


def test_daily_dst_preserves_wall_clock() -> None:
    # America/New_York springs forward 2026-03-08. A daily 09:00 must stay 09:00 local
    # on both sides of the boundary (its UTC offset changes from -5 to -4).
    tz = "America/New_York"
    ny = cadence._tz(tz)
    after = _dt(2026, 3, 7, 20)  # 15:00 EST on 2026-03-07
    nxt = cadence.compute_next(
        cadence_kind="daily",
        after=after,
        anchor=after,
        timezone=tz,
        local_time=datetime.time(9, 0),
    )
    assert nxt is not None
    local = nxt.astimezone(ny)
    assert (local.year, local.month, local.day, local.hour) == (2026, 3, 8, 9)
    assert local.utcoffset() == datetime.timedelta(hours=-4)  # EDT after spring-forward


def test_validate_cadence_errors() -> None:
    with pytest.raises(cadence.CadenceError):
        cadence.validate_cadence(
            "cron",
            cron_expr="not a cron",
            interval_seconds=None,
            weekly_days=None,
            monthly_day=None,
            local_time=None,
        )
    with pytest.raises(cadence.CadenceError):
        cadence.validate_cadence(
            "interval",
            cron_expr=None,
            interval_seconds=10,
            weekly_days=None,
            monthly_day=None,
            local_time=None,
        )
    with pytest.raises(cadence.CadenceError):
        cadence.validate_cadence(
            "weekly",
            cron_expr=None,
            interval_seconds=None,
            weekly_days="0",
            monthly_day=None,
            local_time=None,  # missing local_time
        )
    # Valid ones do not raise.
    cadence.validate_cadence(
        "cron",
        cron_expr="0 9 * * 1-5",
        interval_seconds=None,
        weekly_days=None,
        monthly_day=None,
        local_time=None,
    )


def test_first_fire_at_once_future_only() -> None:
    now = _dt(2026, 7, 23, 10)
    at = cadence.first_fire_at(
        cadence_kind="once", now=now, timezone="UTC", once_at=_dt(2026, 7, 24, 9)
    )
    assert at == _dt(2026, 7, 24, 9)
    with pytest.raises(cadence.CadenceError):
        cadence.first_fire_at(
            cadence_kind="once", now=now, timezone="UTC", once_at=_dt(2026, 7, 23, 9)
        )
