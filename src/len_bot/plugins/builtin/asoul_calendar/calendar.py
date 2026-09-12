"""One ICS source, with source identities and explicit time boundaries.

ICS folding, text escaping and date decoding are adapted from
LEN5010/astrbot_plugin_asoul at 5a945f6. See LICENSE and SOURCE.md.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as wall_time, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .config import CalendarConfig

if TYPE_CHECKING:
    from len_bot.config_store import MemberSettings


class ScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_at: AwareDatetime = Field(description="带明确时区的ISO日期时间，包含起点")
    end_at: AwareDatetime = Field(description="带明确时区的ISO日期时间，不包含终点；须晚于start_at")
    member: str | None = Field(min_length=1, description="已配置成员名称或别名；null读取全部，包括团体署名，团体不展开为个人名单。")

    @model_validator(mode="after")
    def increasing_range(self):
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at; range is [start_at,end_at)")
        return self


class CalendarEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    source_uid: str
    recurrence_id: str | None
    start_at: datetime
    end_at: datetime | None
    duration_seconds: float | None
    all_day: bool
    title: str
    description: str
    location: str
    categories: str
    url: str | None
    status: str | None
    cancelled: bool
    source_updated_at: datetime | None
    source_stamp: datetime | None
    host_signature: str | None


class ScheduleResult(BaseModel):
    source_url: str
    fetched_at: float
    source_updated_at: str | None
    start_at: datetime
    end_at: datetime
    timezone: str
    member: str | None
    cached: bool
    events: list[CalendarEvent]
    coverage: str = "source_calendar_events"
    range_rule: str = "[start_at,end_at); missing event end times are selected by their start time"
    limitation: str = "日历未收录不代表确定没有直播；日程时间不证明实际开播。"


class ScheduleSourceUnavailable(RuntimeError):
    """The configured calendar source did not return usable data this time.

    A source failure is a business state, not an empty schedule: callers must
    present it as such and must not report "no events today" or silently skip a
    group result.
    """

    def __init__(self, message: str, *, source_url: str, attempted_at: float):
        super().__init__(message)
        self.source_url = source_url
        self.attempted_at = attempted_at


@dataclass(frozen=True)
class CalendarSnapshot:
    events: tuple[CalendarEvent, ...]
    fetched_at: float
    source_updated_at: str | None


def unfold_lines(text: str) -> list[str]:
    lines = []
    for raw in text.splitlines():
        if raw.startswith((" ", "\t")):
            if not lines:
                raise ValueError("ICS begins with an invalid folded line")
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def content_line(line: str) -> tuple[str, dict[str, str], str]:
    # A property parameter may contain ':' inside quotes (for example a URI).
    quoted = False
    separator = None
    for index, char in enumerate(line):
        if char == '"':
            quoted = not quoted
        elif char == ":" and not quoted:
            separator = index
            break
    if separator is None:
        raise ValueError("ICS content line has no property separator")
    pieces = line[:separator].split(";")
    params = {}
    for piece in pieces[1:]:
        key, value = piece.split("=", 1)
        params[key.upper()] = value.strip('"')
    return pieces[0].upper(), params, line[separator + 1:]


def decode_text(value: str) -> str:
    replacements = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}
    return re.sub(r"\\([nN,;\\])", lambda match: replacements[match[1]], value)


def parse_datetime(value: str, params: dict[str, str], floating_timezone: ZoneInfo) -> tuple[datetime, bool]:
    if params.get("VALUE") == "DATE":
        day = datetime.strptime(value, "%Y%m%d").date()
        return datetime.combine(day, wall_time.min, floating_timezone), True
    if params.get("VALUE", "DATE-TIME") != "DATE-TIME":
        raise ValueError("Unsupported ICS date value type")
    if value.endswith("Z"):
        if "TZID" in params:
            raise ValueError("UTC ICS time cannot also specify TZID")
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc), False
    zone = ZoneInfo(params["TZID"]) if "TZID" in params else floating_timezone
    return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=zone), False


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"P(?:(\d+)W|(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?)", value)
    if match is None or not any(match.groups()):
        raise ValueError("Unsupported or empty ICS DURATION")
    weeks, days, hours, minutes, seconds = (int(part) if part else 0 for part in match.groups())
    return timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)


def parse_calendar(text: str, floating_timezone: ZoneInfo) -> tuple[CalendarEvent, ...]:
    lines = unfold_lines(text)
    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        raise ValueError("Calendar source did not return a complete VCALENDAR")
    events = []
    current = None
    nested_components = 0
    identities = set()
    for line in lines:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError("Nested VEVENT is invalid")
            current = {}
            continue
        if current is None:
            continue
        if line == "END:VEVENT":
            if nested_components:
                raise ValueError("Unclosed component in VEVENT")
            event = build_event(current, floating_timezone)
            identity = (event.source_uid, event.recurrence_id)
            if identity in identities:
                raise ValueError(f"Duplicate calendar source identity: {event.source_uid}")
            identities.add(identity)
            events.append(event)
            current = None
            continue
        if line.startswith("BEGIN:"):
            nested_components += 1
        elif line.startswith("END:"):
            nested_components -= 1
        elif not nested_components:
            name, params, value = content_line(line)
            current.setdefault(name, []).append((params, value))
    if current is not None:
        raise ValueError("Calendar source contains an unfinished VEVENT")
    return tuple(sorted(events, key=lambda event: (event.start_at, event.source_uid, event.recurrence_id or "")))


def build_event(properties, floating_timezone: ZoneInfo) -> CalendarEvent:
    def one(name, *, required=False):
        values = properties.get(name, [])
        if len(values) > 1 or required and not values:
            raise ValueError(f"ICS event requires exactly one {name}")
        return values[0] if values else None

    def text(name):
        item = one(name)
        return decode_text(item[1]) if item else ""

    def timestamp(name):
        item = one(name)
        return parse_datetime(item[1], item[0], floating_timezone)[0] if item else None

    uid = one("UID", required=True)[1]
    if not uid:
        raise ValueError("ICS event UID cannot be empty")
    # Repeating source events need an explicit occurrence representation.
    # Never silently omit a repetition rule or invent occurrence identities.
    if any(name in properties for name in ("RRULE", "RDATE", "EXDATE")):
        raise ValueError(f"ICS recurrence is not supported for source event {uid}")
    start_params, start_value = one("DTSTART", required=True)
    start_at, all_day = parse_datetime(start_value, start_params, floating_timezone)
    end_at = timestamp("DTEND")
    raw_duration = one("DURATION")
    if raw_duration is not None and end_at is not None:
        raise ValueError("ICS event cannot contain both DTEND and DURATION")
    duration = parse_duration(raw_duration[1]) if raw_duration is not None else None
    if duration is not None:
        end_at = start_at + duration
    if end_at is not None and end_at <= start_at:
        raise ValueError("ICS event end must be later than start")
    recurrence = one("RECURRENCE-ID")
    description = text("DESCRIPTION")
    first_line = description.splitlines()[0] if description else ""
    signature = first_line.split("|", 1)[1].strip() if "|" in first_line else None
    status = text("STATUS") or None
    return CalendarEvent(
        source_uid=uid, recurrence_id=recurrence[1] if recurrence else None,
        start_at=start_at, end_at=end_at, duration_seconds=duration.total_seconds() if duration is not None else None,
        all_day=all_day, title=text("SUMMARY"), description=description,
        location=text("LOCATION"), categories=text("CATEGORIES"), url=text("URL") or None,
        status=status, cancelled=status == "CANCELLED", source_updated_at=timestamp("LAST-MODIFIED"),
        source_stamp=timestamp("DTSTAMP"),
        host_signature=signature,
    )


class CalendarService:
    def __init__(self, config: CalendarConfig, timezone_name: str, members: list[MemberSettings]):
        self.config = config
        self.zone = ZoneInfo(timezone_name)
        self.members = tuple(members)
        self._client = httpx.AsyncClient(timeout=config.request_timeout_seconds, trust_env=False,
            follow_redirects=False, headers={"User-Agent": config.user_agent})
        self._snapshot: CalendarSnapshot | None = None
        self._lock = asyncio.Lock()
        self.last_success_at = None
        self.last_error_at = None
        self.last_error = None

    async def close(self):
        await self._client.aclose()

    async def snapshot(self) -> tuple[CalendarSnapshot, bool]:
        async with self._lock:
            if self._snapshot and time.time() < self._snapshot.fetched_at + self.config.cache_ttl_seconds:
                return self._snapshot, True
            try:
                response = await self._client.get(self.config.source_url)
                response.raise_for_status()
                events = parse_calendar(response.content.decode("utf-8-sig"), self.zone)
            except Exception as error:
                self.last_error_at = time.time()
                self.last_error = f"{type(error).__name__}: {error}"
                raise ScheduleSourceUnavailable(f"{type(error).__name__}: {error}",
                    source_url=self.config.source_url, attempted_at=self.last_error_at) from error
            fetched_at = time.time()
            self._snapshot = CalendarSnapshot(events, fetched_at, response.headers.get("last-modified"))
            self.last_success_at = fetched_at
            return self._snapshot, False

    def member_aliases(self, name: str) -> tuple[str, ...]:
        query = name.casefold()
        matches = [member for member in self.members
                   if query in {member.name.casefold(), *(alias.casefold() for alias in member.aliases)}]
        if len(matches) != 1:
            raise ValueError("Unknown or ambiguous configured calendar member")
        member = matches[0]
        return tuple(value.casefold() for value in (member.name, *member.aliases))

    def is_live_event(self, event: CalendarEvent) -> bool:
        if event.all_day and not self.config.include_all_day:
            return False
        if event.url and "live.bilibili.com" in event.url:
            return True
        text = " ".join((event.title, event.categories, event.location)).casefold()
        if any(keyword.casefold() in text for keyword in self.config.live_keywords):
            return True
        return not any(keyword.casefold() in text for keyword in self.config.non_live_keywords)

    async def query(self, request: ScheduleRequest) -> ScheduleResult:
        aliases = self.member_aliases(request.member) if request.member is not None else None
        snapshot, cached = await self.snapshot()
        selected = []
        for event in snapshot.events:
            if not self.is_live_event(event):
                continue
            if event.end_at is None:
                overlaps = request.start_at <= event.start_at < request.end_at
            else:
                overlaps = event.start_at < request.end_at and event.end_at > request.start_at
            if not overlaps:
                continue
            if aliases is not None:
                signature = " ".join((event.title, event.host_signature or "")).casefold()
                if not any(alias in signature for alias in aliases):
                    continue
            selected.append(event)
        return ScheduleResult(source_url=self.config.source_url, fetched_at=snapshot.fetched_at,
            source_updated_at=snapshot.source_updated_at, start_at=request.start_at, end_at=request.end_at,
            timezone=self.zone.key, member=request.member, cached=cached, events=selected)

    def source_status(self) -> dict:
        return {"source_url": self.config.source_url, "last_success_at": self.last_success_at,
                "last_error_at": self.last_error_at, "last_error": self.last_error,
                "source_updated_at": self._snapshot.source_updated_at if self._snapshot else None}

    def source_failure(self, *, attempted_at: float | None = None) -> ScheduleSourceUnavailable | None:
        """The most recent real source failure, if the source did not succeed after it."""
        if self.last_error_at is None or (self.last_success_at is not None and self.last_success_at >= self.last_error_at):
            return None
        return ScheduleSourceUnavailable(self.last_error or '日程来源本次未取得',
            source_url=self.config.source_url, attempted_at=attempted_at if attempted_at is not None else self.last_error_at)
