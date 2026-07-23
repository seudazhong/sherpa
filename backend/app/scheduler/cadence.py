"""Cadence engine for recurring schedules (ADR-031, Phase CRON).

Pure, tz-aware next-occurrence computation for every ``cadence_kind``:
``daily`` / ``cron`` / ``interval`` / ``weekly`` / ``monthly`` / ``once``. Given the
slot that just fired (``anchor``) and "now" (``after``), return the next fire moment
strictly after ``after`` as an aware UTC datetime, or ``None`` when the schedule is
terminal (``once``). Cron/weekly/monthly are evaluated in the schedule's IANA
timezone so DST shifts are handled correctly; the result is converted back to UTC.

Kept ORM-free so it is unit-testable in isolation; :func:`next_for` adapts a
``Schedule`` row onto :func:`compute_next`.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter

# Hard floor also enforced by a DB CHECK; the service may impose a higher floor.
MIN_INTERVAL_SECONDS = 60
_CADENCE_KINDS = frozenset({"daily", "cron", "interval", "weekly", "monthly", "once"})


class CadenceError(ValueError):
    """Invalid cadence configuration (surfaced as a service ``Invalid``)."""


def _tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CadenceError(f"invalid timezone: {name}") from exc


def parse_weekly_days(csv: str) -> list[int]:
    """Parse a CSV of weekday ints (0=Mon .. 6=Sun) into a sorted unique list."""
    days: set[int] = set()
    for part in csv.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            day = int(part)
        except ValueError as exc:
            raise CadenceError(f"invalid weekday: {part!r}") from exc
        if not 0 <= day <= 6:
            raise CadenceError(f"weekday out of range 0..6: {day}")
        days.add(day)
    if not days:
        raise CadenceError("weekly_days must list at least one day")
    return sorted(days)


def validate_cadence(
    cadence_kind: str,
    *,
    cron_expr: str | None,
    interval_seconds: int | None,
    weekly_days: str | None,
    monthly_day: int | None,
    local_time: datetime.time | None,
    min_interval_seconds: int = MIN_INTERVAL_SECONDS,
) -> None:
    """Raise :class:`CadenceError` if the cadence fields are inconsistent."""
    if cadence_kind not in _CADENCE_KINDS:
        raise CadenceError(f"invalid cadence_kind: {cadence_kind}")
    if cadence_kind == "cron":
        if not cron_expr or not croniter.is_valid(cron_expr):
            raise CadenceError("cron_expr is required and must be a valid 5-field cron")
    elif cadence_kind == "interval":
        if interval_seconds is None:
            raise CadenceError("interval_seconds is required")
        if interval_seconds < min_interval_seconds:
            raise CadenceError(f"interval_seconds must be >= {min_interval_seconds}")
    elif cadence_kind == "weekly":
        if not weekly_days:
            raise CadenceError("weekly_days is required")
        parse_weekly_days(weekly_days)
        if local_time is None:
            raise CadenceError("weekly cadence requires local_time")
    elif cadence_kind == "monthly":
        if monthly_day is None or not 1 <= monthly_day <= 31:
            raise CadenceError("monthly_day must be 1..31")
        if local_time is None:
            raise CadenceError("monthly cadence requires local_time")
    elif cadence_kind == "daily":
        if local_time is None:
            raise CadenceError("daily cadence requires local_time")


def _at_local(day: datetime.date, local_time: datetime.time, tz: ZoneInfo) -> datetime.datetime:
    return datetime.datetime.combine(day, local_time, tzinfo=tz)


def _next_daily(
    after_local: datetime.datetime, local_time: datetime.time, tz: ZoneInfo
) -> datetime.datetime:
    candidate = _at_local(after_local.date(), local_time, tz)
    if candidate <= after_local:
        candidate = _at_local(after_local.date() + datetime.timedelta(days=1), local_time, tz)
    return candidate


def _next_weekly(
    after_local: datetime.datetime, days: list[int], local_time: datetime.time, tz: ZoneInfo
) -> datetime.datetime:
    for offset in range(0, 8):
        day = after_local.date() + datetime.timedelta(days=offset)
        if day.weekday() in days:
            candidate = _at_local(day, local_time, tz)
            if candidate > after_local:
                return candidate
    raise CadenceError("could not compute next weekly occurrence")  # unreachable


def _next_monthly(
    after_local: datetime.datetime, monthly_day: int, local_time: datetime.time, tz: ZoneInfo
) -> datetime.datetime:
    year, month = after_local.year, after_local.month
    for _ in range(0, 48):  # bounded search across months (skips short months)
        try:
            day = datetime.date(year, month, monthly_day)
        except ValueError:
            day = None
        if day is not None:
            candidate = _at_local(day, local_time, tz)
            if candidate > after_local:
                return candidate
        month += 1
        if month > 12:
            month, year = 1, year + 1
    raise CadenceError("could not compute next monthly occurrence")  # unreachable


def compute_next(
    *,
    cadence_kind: str,
    after: datetime.datetime,
    anchor: datetime.datetime,
    timezone: str,
    local_time: datetime.time | None = None,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    weekly_days: str | None = None,
    monthly_day: int | None = None,
) -> datetime.datetime | None:
    """Next fire moment strictly after ``after`` (aware UTC), or None if terminal.

    ``anchor`` is the slot that just fired (the schedule's current ``next_fire_at``);
    it anchors the ``interval`` grid so cadence does not drift.
    """
    if after.tzinfo is None:
        after = after.replace(tzinfo=datetime.UTC)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=datetime.UTC)

    if cadence_kind == "once":
        return None

    if cadence_kind == "interval":
        if interval_seconds is None or interval_seconds < MIN_INTERVAL_SECONDS:
            raise CadenceError("invalid interval_seconds")
        step = datetime.timedelta(seconds=interval_seconds)
        nxt = anchor + step
        while nxt <= after:
            nxt += step
        return nxt.astimezone(datetime.UTC)

    if cadence_kind == "cron":
        if not cron_expr:
            raise CadenceError("cron_expr required")
        tz = _tz(timezone)
        itr = croniter(cron_expr, after.astimezone(tz))
        nxt_local: datetime.datetime = itr.get_next(datetime.datetime)
        return nxt_local.astimezone(datetime.UTC)

    tz = _tz(timezone)
    after_local = after.astimezone(tz)
    if cadence_kind == "daily":
        if local_time is None:
            raise CadenceError("daily requires local_time")
        return _next_daily(after_local, local_time, tz).astimezone(datetime.UTC)
    if cadence_kind == "weekly":
        if weekly_days is None or local_time is None:
            raise CadenceError("weekly requires weekly_days + local_time")
        days = parse_weekly_days(weekly_days)
        return _next_weekly(after_local, days, local_time, tz).astimezone(datetime.UTC)
    if cadence_kind == "monthly":
        if monthly_day is None or local_time is None:
            raise CadenceError("monthly requires monthly_day + local_time")
        return _next_monthly(after_local, monthly_day, local_time, tz).astimezone(datetime.UTC)

    raise CadenceError(f"invalid cadence_kind: {cadence_kind}")


def first_fire_at(
    *,
    cadence_kind: str,
    now: datetime.datetime,
    timezone: str,
    local_time: datetime.time | None = None,
    cron_expr: str | None = None,
    interval_seconds: int | None = None,
    weekly_days: str | None = None,
    monthly_day: int | None = None,
    once_at: datetime.datetime | None = None,
) -> datetime.datetime:
    """Compute the first fire moment at creation time (strictly future)."""
    if cadence_kind == "once":
        if once_at is None:
            raise CadenceError("once cadence requires an absolute time")
        at = once_at if once_at.tzinfo else once_at.replace(tzinfo=datetime.UTC)
        if at <= now:
            raise CadenceError("once time must be in the future")
        return at.astimezone(datetime.UTC)
    if cadence_kind == "interval":
        if interval_seconds is None or interval_seconds < MIN_INTERVAL_SECONDS:
            raise CadenceError("invalid interval_seconds")
        return (now + datetime.timedelta(seconds=interval_seconds)).astimezone(datetime.UTC)
    nxt = compute_next(
        cadence_kind=cadence_kind,
        after=now,
        anchor=now,
        timezone=timezone,
        local_time=local_time,
        cron_expr=cron_expr,
        weekly_days=weekly_days,
        monthly_day=monthly_day,
    )
    if nxt is None:
        raise CadenceError("cadence produced no next occurrence")
    return nxt
