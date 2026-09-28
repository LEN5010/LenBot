"""Strict local-clock cron parsing and next real UTC occurrence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
from zoneinfo import ZoneInfo


_CRON = re.compile(r"cron:(\S+) (\S+) (\S+) (\S+) (\S+)\Z")
_ITEM = re.compile(r"([0-9]{1,2})(?:-([0-9]{1,2}))?(?:/([1-9][0-9]*))?\Z")
_STEP_ALL = re.compile(r"\*/([1-9][0-9]*)\Z")
# (name, lowest, highest); weekday 0 is Sunday as in common cron.
_FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day", 1, 31), ("month", 1, 12), ("weekday", 0, 6))
# Long enough to reach a February 29 across a skipped century leap year.
_SEARCH_DAYS = 366 * 9


@dataclass(frozen=True, slots=True)
class Cron:
    expression: str
    minutes: tuple[int, ...]
    hours: tuple[int, ...]
    days: frozenset[int] | None
    months: frozenset[int]
    weekdays: frozenset[int] | None


class CronTimeError(ValueError):
    """The next requested local time is missing or repeated, or never occurs."""


def _field(text: str, name: str, low: int, high: int) -> tuple[frozenset[int], bool]:
    """Return the selected values and whether the field was restricted."""
    if text == "*":
        return frozenset(range(low, high + 1)), False
    step_all = _STEP_ALL.fullmatch(text)
    if step_all is not None:
        return frozenset(range(low, high + 1, int(step_all.group(1)))), True
    values: set[int] = set()
    for item in text.split(","):
        match = _ITEM.fullmatch(item)
        if match is None:
            raise ValueError(f"cron {name} item {item!r} is not a number, a-b range or a-b/step")
        first = int(match.group(1))
        last = first if match.group(2) is None else int(match.group(2))
        if match.group(3) is not None and match.group(2) is None:
            raise ValueError(f"cron {name} step needs a range or *: {item!r}")
        step = 1 if match.group(3) is None else int(match.group(3))
        if not low <= first <= last <= high:
            raise ValueError(f"cron {name} {item!r} is outside {low}..{high} or reversed")
        values.update(range(first, last + 1, step))
    return frozenset(values), True


def parse_cron(value: str) -> Cron:
    """Parse cron:M H DOM MON DOW with numbers, lists, ranges and steps only."""
    match = _CRON.fullmatch(value)
    if match is None:
        raise ValueError(f"cron must be exactly 'cron:<minute> <hour> <day> <month> <weekday>': {value[:200]!r}")
    parsed = [_field(text, *spec) for text, spec in zip(match.groups(), _FIELDS, strict=True)]
    (minutes, _), (hours, _), (days, days_set), (months, _), (weekdays, weekdays_set) = parsed
    if days_set and weekdays_set:
        raise ValueError("cron cannot restrict both day of month and weekday; use two schedules")
    return Cron(value, tuple(sorted(minutes)), tuple(sorted(hours)),
                days if days_set else None, months, weekdays if weekdays_set else None)


def _matches(cron: Cron, day: date) -> bool:
    return (day.month in cron.months
            and (cron.days is None or day.day in cron.days)
            and (cron.weekdays is None or (day.isoweekday() % 7) in cron.weekdays))


def next_cron(cron: Cron, timezone: str, after: float) -> float:
    """Return the first real occurrence strictly after ``after``.

    A future missing or repeated local occurrence is an explicit error, not a
    shifted time, a chosen fold, or two deliveries.
    """
    zone = ZoneInfo(timezone)
    after_local = datetime.fromtimestamp(after, zone)
    day = after_local.date()
    for _ in range(_SEARCH_DAYS):
        if _matches(cron, day):
            for hour in cron.hours:
                for minute in cron.minutes:
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
                        raise CronTimeError(f"{cron.expression} at {wall.isoformat()} in {timezone} is repeated: {examples}")
                    if not candidates and wall > after_local.replace(tzinfo=None):
                        raise CronTimeError(f"{cron.expression} at {wall.isoformat()} in {timezone} does not exist")
                    if future:
                        return min(future)
        try:
            day += timedelta(days=1)
        except OverflowError as error:
            raise CronTimeError(f"No representable future time for {cron.expression} in {timezone}") from error
    raise CronTimeError(f"{cron.expression} has no occurrence within {_SEARCH_DAYS} days in {timezone}")
