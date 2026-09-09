"""Calendar reads and command rendering; Runtime owns every publication."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import ToolResult, ToolSource

from .calendar import CalendarService, ScheduleRequest, ScheduleResult
from .config import CalendarCommand, CalendarConfig
from .render import ScheduleRenderer

if TYPE_CHECKING:
    from len_bot.config_store import MemberSettings, TimeSettings


@dataclass(frozen=True)
class RenderedSchedule:
    png: bytes
    schedule: ScheduleResult


class AsoulCalendarPlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.config: CalendarConfig = context.config
        self.time_settings = context.time_settings
        members = context.members
        self.member_keywords = tuple(value for member in members for value in (member.name, *member.aliases))
        self.service = CalendarService(self.config, self.time_settings.timezone, members)
        self.renderer = ScheduleRenderer(self.config, resource_directory=context.directory)

    async def on_load(self, context: PluginContext):
        context.register_tool(name="get_live_schedule",
            description=(f"读取源直播日程。业务时区为{self.time_settings.timezone}，"
                f"每周从周{'一二三四五六日'[self.time_settings.week_start]}开始。"
                "start_at/end_at须带时区，范围为[start_at,end_at)。返回源UID、时间、取消状态与取得时刻；日程不证明实际开播。"),
            parameter_model=ScheduleRequest, handler=self.get_live_schedule, kind="read",
            purpose="查询直播日程与排期", aliases=("日程", "直播日历", *[word for words in self.config.commands.values() for word in words]),
            keywords=("直播", "这周", "本周", "今天", "明天", "安排", "排期", "时间表", *self.member_keywords),
            roles=("conversation", "work"), deferred=False)

    async def on_unload(self):
        await self.service.close()

    async def get_live_schedule(self, request: ScheduleRequest, call_context: PluginCallContext) -> ToolResult:
        schedule = await self.service.query(request)
        return ToolResult(content=schedule.model_dump_json(), evidence_kind="external",
            coverage=schedule.coverage, status="ok" if schedule.events else "no_results",
            fetched_at=schedule.fetched_at, cached=schedule.cached,
            sources=[ToolSource(url=schedule.source_url, title="A-SOUL 源日历",
                                published_at=schedule.source_updated_at)])

    def match_command(self, text: str) -> CalendarCommand | None:
        command_text = text.strip()
        for command, words in self.config.commands.items():
            if command_text in words:
                return command
        return None

    async def render_command(self, command: CalendarCommand, now: float) -> RenderedSchedule:
        zone = ZoneInfo(self.time_settings.timezone)
        today = datetime.fromtimestamp(now, zone).date()
        if command == "calendar_today":
            start_day, days, title = today, 1, "今日直播"
        elif command == "calendar_tomorrow":
            start_day, days, title = today + timedelta(days=1), 1, "明日直播"
        elif command == "calendar_week":
            start_day = today - timedelta(days=(today.weekday() - self.time_settings.week_start) % 7)
            days, title = 7, "本周直播"
        else:
            raise ValueError("Unknown calendar command")
        start_at = datetime.combine(start_day, time.min, zone)
        schedule = await self.service.query(ScheduleRequest(start_at=start_at,
            end_at=start_at + timedelta(days=days), member=None))
        png = await asyncio.to_thread(self.renderer.render, schedule, title)
        return RenderedSchedule(png=png, schedule=schedule)

    def source_status(self) -> dict:
        return self.service.source_status()
