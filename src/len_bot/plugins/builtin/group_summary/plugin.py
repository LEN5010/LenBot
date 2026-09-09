"""Natural-language report creation and same-group saved-report reads."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator

from len_bot.plugins.api import BasePlugin, PluginContext, PluginCallContext, ToolResult

from .config import GroupSummaryConfig
from .service import GroupSummaryService
from .work import GroupSummaryRange, summary_goal


class ReportWindow(BaseModel):
    model_config=ConfigDict(extra='forbid')
    mode: Literal['today','yesterday','date','range'] = Field(description='今天、昨天必须选相应自然日；明确日期用date，明确时段才用range')
    day: date | None = Field(default=None,description='mode=date时填写YYYY-MM-DD，其他模式不填')
    start_at: AwareDatetime | None = Field(default=None,description='只有range模式填写带时区的起点')
    end_at: AwareDatetime | None = Field(default=None,description='只有range模式填写带时区的终点，不包含')

    @model_validator(mode='after')
    def selected_fields(self):
        if (self.mode=='date')!=(self.day is not None):raise ValueError('Only date mode requires day')
        if self.mode=='range':
            if self.start_at is None or self.end_at is None or self.start_at>=self.end_at:
                raise ValueError('Range mode requires ordered, timezone-aware start_at and end_at')
        elif self.start_at is not None or self.end_at is not None:
            raise ValueError('Natural-day modes do not accept model-calculated interval endpoints')
        return self

    def bounds(self,source_timestamp,timezone):
        zone=ZoneInfo(timezone)
        if self.mode=='range':return self.start_at.astimezone(zone),self.end_at.astimezone(zone)
        day=self.day if self.mode=='date' else datetime.fromtimestamp(source_timestamp,zone).date()
        if self.mode=='yesterday':day-=timedelta(days=1)
        return datetime.combine(day,time.min,zone),datetime.combine(day+timedelta(days=1),time.min,zone)


class SummarizeArguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
    window: ReportWindow
    focus: str = Field(description='关注的事件或话题；空字符串表示主要讨论')
    request_source: str = Field(description='提出此报告请求的已读人类消息M；自然日按该原话时间计算')
    evidence: list[str] = Field(min_length=1,description='本轮已完整读过的请求原话M')


class ReadWindowArguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
    cursor: str | None = Field(description='首次为null，之后原样复制当前工作返回的next_cursor')


class ReadReportArguments(BaseModel):
    model_config=ConfigDict(extra='forbid')
    window: ReportWindow
    revision: int | None = Field(default=None,ge=1,description='指定原报告工作版本；null读取该范围最新保存的报告')


class GroupSummaryPlugin(BasePlugin):
    def __init__(self,context: PluginContext):
        super().__init__(context.manifest)
        self.config: GroupSummaryConfig=context.config
        self.context=context
        self.service=GroupSummaryService(context.event_store,self.config)

    async def on_load(self,context: PluginContext):
        if not (Path(context.directory)/self.config.render_font_path).is_file():
            raise ValueError('报告字体文件不存在，请在插件配置中填写 render_font_path')
        context.register_tool('summarize_group_chat',
            '暂存本群按需增量报告工作。插件安排批次分析、复用与合并，程序计算统计并渲染图片，完成后沿原工作交付。'
            '今天/昨天必须用window对应自然日，明确日期用date；只有明确时段才用range。查询已生成报告用read_group_report。',
            SummarizeArguments,self.summarize,purpose='生成本群固定范围的结构化群聊报告',
            aliases=('群总结','群日报','总结群聊','今天群里发生了什么'),keywords=('群聊','总结','日报','回顾'),
            kind='proposal',roles=('conversation',))
        context.register_tool('read_group_chat_window',
            '读取当前总结工作固定范围的原话页，按时间排序；这是资料读取，不推进成功分析批次。',
            ReadWindowArguments,self.read_window,purpose='读取本群报告工作的固定原话范围',
            aliases=('群聊窗口',),keywords=('原话','分页','群总结'),kind='read',roles=('work',),
            available=lambda call:call.job_id is not None and call.work_operation=='group_summary',page_chars=self.config.page_chars)
        context.register_tool('read_group_report',
            '读取本群指定自然日或精确时段已经保存的结构化报告和图片引用；不分析、不渲染、不自动发送。'
            '范围不相同的日报不能冒充本次时段结果；派生结论与原话来源分别保留。',
            ReadReportArguments,self.read_report,purpose='查询本群已生成的报告与历史图片',
            aliases=('查看群日报','历史群总结','已有日报'),keywords=('日报','历史','报告','图片','已生成'),
            kind='read',roles=('conversation','work'),deferred=True,page_chars=self.config.page_chars)

    async def summarize(self,values: SummarizeArguments,call: PluginCallContext):
        try:
            if not call.scene_id.startswith('group:'):raise ValueError('群报告只能使用当前群')
            source=await call.read_request_source(values.request_source)
            timezone=self.context.time_settings.timezone
            start,end=values.window.bounds(source.timestamp,timezone)
            request=GroupSummaryRange(start_at=start,end_at=end,timezone=timezone,focus=values.focus,
                snapshot_rowid=call.cutoff_rowid,snapshot_at=call.now,bot_actor_id=self.context.bot_actor_id,
                analysis_instructions=self.config.output_instructions)
            for job in await self.context.event_store.list_jobs(call.scene_id):
                owner=job['plugin_origin']
                if not owner or owner['plugin_id']!=self.manifest.id or owner['plugin_version']!=self.manifest.version:continue
                if job['status'] not in {'pending','claimed','processing'}:continue
                current=GroupSummaryRange.model_validate(job['work_parameters'])
                if current.start_at==start and current.end_at==end and current.focus==values.focus:
                    return ToolResult(status='ok',evidence_kind='retrieval',coverage='existing_report_work',
                        content=json.dumps({'job_id':job['id'],'revision':job['revision'],'status':job['status'],
                            'range':current.model_dump(mode='json'),
                            'note':'同一范围已有报告工作正在执行，未创建第二份或重置预算；快照仍为所示时间。'},ensure_ascii=False))
            return await call.stage_work(goal=summary_goal(request),request_source=values.request_source,
                evidence=values.evidence,parameters=request)
        except ValidationError as error:
            return ToolResult.validation_failure(error,tool_name='summarize_group_chat',tool_call_id=call.tool_call_id)
        except ValueError as error:
            return ToolResult.failure(str(error),'invalid_reference',stage='references',
                tool_name='summarize_group_chat',tool_call_id=call.tool_call_id)

    async def read_window(self,values: ReadWindowArguments,call: PluginCallContext):
        return await self.service.read_window(values.cursor,call)

    async def read_report(self,values: ReadReportArguments,call: PluginCallContext):
        sources=await self.context.event_store.events_by_ids(call.scene_id,[call.source_event_id],call.cutoff_rowid)
        if len(sources)!=1:raise ValueError('Report lookup requires its real current-scene source')
        start,end=values.window.bounds(sources[0].timestamp,self.context.time_settings.timezone)
        return await self.service.read_report(call,start,end,values.revision)
