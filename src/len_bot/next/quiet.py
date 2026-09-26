"""Resolve one configured local quiet interval to its actual UTC bounds."""

from datetime import datetime, timedelta, timezone as DateTimeZone
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


def quiet_period(settings: QuietHours | None, timezone: str, now: float) -> tuple[float, float] | None:
    """Return the containing [start, end) in UTC epoch seconds, if any."""
    if settings is None:
        return None
    zone = ZoneInfo(timezone)
    today = datetime.fromtimestamp(now, zone).date()
    crosses_midnight = settings.end < settings.start
    for start_day in (today, today - timedelta(days=1)):
        end_day = start_day + timedelta(days=1) if crosses_midnight else start_day
        start = _boundary(datetime.combine(start_day, settings.start), zone, later=False)
        end = _boundary(datetime.combine(end_day, settings.end), zone, later=True)
        if start < end and start <= now < end:
            return start, end
    return None


def next_quiet_start(settings: QuietHours | None, timezone: str, after: float) -> float | None:
    """Find the first nonempty quiet interval beginning strictly after an instant."""
    if settings is None:
        return None
    zone = ZoneInfo(timezone)
    start_day = datetime.fromtimestamp(after, zone).date()
    crosses_midnight = settings.end < settings.start
    while True:
        end_day = start_day + timedelta(days=1) if crosses_midnight else start_day
        start = _boundary(datetime.combine(start_day, settings.start), zone, later=False)
        end = _boundary(datetime.combine(end_day, settings.end), zone, later=True)
        if start < end and start > after:
            return start
        start_day += timedelta(days=1)
