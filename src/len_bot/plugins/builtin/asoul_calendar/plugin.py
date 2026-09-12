"""Calendar reads and command rendering; Runtime owns every publication."""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import ExactText, PluginCallContext
from len_bot.media.models import MessageSegment
from len_bot.tools.results import ToolResult, ToolSource

from .calendar import CalendarService, ScheduleRequest, ScheduleResult
from .config import CalendarCommand, CalendarConfig
from .render import ScheduleRenderer

if TYPE_CHECKING:
    from len_bot.config_store import MemberSettings, TimeSettings


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
        for command, words in self.config.commands.items():
            if words:
                context.register_handler(id=command, description='精确日程命令：' + ' / '.join(words),
                    match=ExactText(tuple(words)), handler=self.on_command, priority=10, consume=True,
                    available=lambda call, command=command: bool(call.scene_config and command in call.scene_config.commands))
        context.register_handler(id='calendar_comment', description='记录引用本插件日程结果的评论并继续普通聊天处理',
            match=self.match_comment, handler=self.on_comment, priority=20, consume=False)

    async def on_unload(self):
        await self.service.close()

    async def get_live_schedule(self, request: ScheduleRequest, call_context: PluginCallContext) -> ToolResult:
        schedule = await self.service.query(request)
        return ToolResult(content=schedule.model_dump_json(), evidence_kind="external",
            coverage=schedule.coverage, status="ok" if schedule.events else "no_results",
            fetched_at=schedule.fetched_at, cached=schedule.cached,
            sources=[ToolSource(url=schedule.source_url, title="A-SOUL 源日历",
                                published_at=schedule.source_updated_at)])

    def match_comment(self, call: PluginCallContext) -> bool:
        quote = call.event.metadata.get('quote_context', {})
        origin = quote.get('plugin_origin') or {}
        return (origin.get('plugin_id') == self.manifest.id
            or any(route['origin']['plugin_id'] == self.manifest.id for route in quote.get('plugin_routes', ()))
            )

    async def on_comment(self, call: PluginCallContext):
        # A comment is saved and consumed; this plugin defines no reply to it.
        return None

    def command_request(self, command: CalendarCommand, now: float) -> tuple[ScheduleRequest, str]:
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
        return ScheduleRequest(start_at=start_at, end_at=start_at + timedelta(days=days), member=None), title

    async def on_command(self, call: PluginCallContext):
        request, title = self.command_request(call.origin.entry_id, call.event.timestamp)
        observed = await call.invoke_tool('get_live_schedule', request)
        if observed.status not in {'ok', 'no_results'}:
            raise ValueError(f'日程来源未完整取得：{observed.content}')
        schedule = ScheduleResult.model_validate_json(observed.content)
        png = await asyncio.to_thread(self.renderer.render, schedule, title)
        asset_id = await call.save_image(png, '日程命令生成图片')
        await call.submit_message([MessageSegment(type='image', asset_id=asset_id)])

    def source_status(self) -> dict:
        return self.service.source_status()
