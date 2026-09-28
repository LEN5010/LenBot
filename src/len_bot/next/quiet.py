"""Resolve configured daily local intervals to their actual UTC bounds."""

from datetime import datetime, time as WallTime, timedelta, timezone as DateTimeZone
from math import ceil, floor
from zoneinfo import ZoneInfo

from .config import QuietHours


def _boundary(local: datetime, zone: ZoneInfo, *, later: bool) -> float:
    candidates = [local.replace(tzinfo=zone, fold=fold).astimezone(DateTimeZone.utc).timestamp()
                  for fold in (0, 1)]
    valid = [instant for instant in candidates
             if datetime.fromtimestamp(instant, zone).replace(tzinfo=None) == local]
    if valid:
        return max(valid) if later else min(valid)

    # A nonexistent wall time lies across an offset jump. Find its first real
    # instant, rather than shifting the requested minute by the gap duration.
    lower, upper = floor(min(candidates)), ceil(max(candidates))
    earlier_offset = datetime.fromtimestamp(lower, zone).utcoffset()
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if datetime.fromtimestamp(middle, zone).utcoffset() == earlier_offset:
            lower = middle
        else:
            upper = middle
    return float(upper)


def local_period(start_clock: WallTime, end_clock: WallTime, timezone: str,
                 now: float) -> tuple[float, float] | None:
    """Return the daily local [start, end) containing ``now`` in UTC epoch seconds, if any."""
    zone = ZoneInfo(timezone)
    today = datetime.fromtimestamp(now, zone).date()
    crosses_midnight = end_clock < start_clock
    for start_day in (today, today - timedelta(days=1)):
        end_day = start_day + timedelta(days=1) if crosses_midnight else start_day
        start = _boundary(datetime.combine(start_day, start_clock), zone, later=False)
        end = _boundary(datetime.combine(end_day, end_clock), zone, later=True)
        if start < end and start <= now < end:
            return start, end
    return None


def next_local_start(start_clock: WallTime, end_clock: WallTime, timezone: str, after: float) -> float:
    """Find the first nonempty daily local interval beginning strictly after an instant."""
    zone = ZoneInfo(timezone)
    start_day = datetime.fromtimestamp(after, zone).date()
    crosses_midnight = end_clock < start_clock
    while True:
        end_day = start_day + timedelta(days=1) if crosses_midnight else start_day
        start = _boundary(datetime.combine(start_day, start_clock), zone, later=False)
        end = _boundary(datetime.combine(end_day, end_clock), zone, later=True)
        if start < end and start > after:
            return start
        start_day += timedelta(days=1)


def quiet_period(settings: QuietHours | None, timezone: str, now: float) -> tuple[float, float] | None:
    """Return the containing [start, end) in UTC epoch seconds, if any."""
    if settings is None:
        return None
    return local_period(settings.start, settings.end, timezone, now)


def next_quiet_start(settings: QuietHours | None, timezone: str, after: float) -> float | None:
    """Find the first nonempty quiet interval beginning strictly after an instant."""
    if settings is None:
        return None
    return next_local_start(settings.start, settings.end, timezone, after)
