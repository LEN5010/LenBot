"""Strict daily local-clock cron parsing and next real UTC occurrence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo


_DAILY = re.compile(r"cron:([0-9]{1,2}) ([0-9]{1,2}) \* \* \*\Z")


@dataclass(frozen=True, slots=True)
class DailyCron:
    minute_of_day: int


class CronTimeError(ValueError):
    """The requested daily local time is missing or repeated."""


def parse_daily_cron(value: str) -> DailyCron:
    match = _DAILY.fullmatch(value)
    if match is None:
        raise ValueError(f"Only daily cron:M H * * * is supported: {value[:200]!r}")
    minute, hour = (int(part, 10) for part in match.groups())
    if minute > 59 or hour > 23:
        raise ValueError(f"Daily cron minute/hour is outside 0..59/0..23: {value[:200]!r}")
    return DailyCron(hour * 60 + minute)


def format_daily_cron(minute_of_day: int) -> str:
    hour, minute = divmod(minute_of_day, 60)
    return f"cron:{minute} {hour} * * *"


def next_daily_cron(minute_of_day: int, timezone: str, after: float) -> float:
    """Return the first real daily occurrence strictly after ``after``.

    A future missing or repeated local occurrence is an explicit error, not a
    shifted time, a chosen fold, or two deliveries.
    """
    zone = ZoneInfo(timezone)
    after_local = datetime.fromtimestamp(after, zone)
    hour, minute = divmod(minute_of_day, 60)
    day = after_local.date()
    while True:
        wall = datetime.combine(day, time(hour, minute))
        candidates: dict[float, datetime] = {}
        for fold in (0, 1):
            instant = wall.replace(tzinfo=zone, fold=fold).timestamp()
            local = datetime.fromtimestamp(instant, zone)
            if local.replace(tzinfo=None) == wall:
                candidates[instant] = local
        future = [instant for instant in candidates if instant > after]
        if len(candidates) == 2 and future:
            examples = ", ".join(local.isoformat() for local in candidates.values())
            raise CronTimeError(
                f"Daily cron {wall.isoformat()} in {timezone} is repeated: {examples}"
            )
        if not candidates and wall > after_local.replace(tzinfo=None):
            raise CronTimeError(f"Daily cron {wall.isoformat()} in {timezone} does not exist")
        if future:
            return min(future)
        try:
            day += timedelta(days=1)
        except OverflowError as error:
            raise CronTimeError(f"No representable future daily cron in {timezone}") from error
