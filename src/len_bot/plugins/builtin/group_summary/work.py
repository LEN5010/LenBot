"""Fixed source range and separate reading, analysis and rendering progress."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from len_bot.plugins.work import PluginWorkSpec, PluginWorkRevision


class GroupSummaryRange(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    start_at: AwareDatetime = Field(title='开始时间（含）')
    end_at: AwareDatetime = Field(title='结束时间（不含）')
    timezone: str = Field(title='本报告使用的业务时区')
    snapshot_rowid: int = Field(ge=0,title='原始资料截点')
    snapshot_at: float = Field(title='数据快照时间',json_schema_extra={'format':'unix-time'})
    bot_actor_id: str = Field(title='排除的机器人账号')
    focus: str = Field(title='关注内容')
    analysis_instructions: str = Field(title='开始时的分析要求')

    @model_validator(mode='after')
    def ordered(self):
        from zoneinfo import ZoneInfo
        ZoneInfo(self.timezone)
        if self.start_at >= self.end_at:raise ValueError('Summary range must satisfy start_at < end_at')
        return self


class SummaryRevision(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start_at: AwareDatetime | None = Field(default=None,title='新的开始时间（含）',description='填写带时区的 ISO 时间，修改时需同时填写结束时间')
    end_at: AwareDatetime | None = Field(default=None,title='新的结束时间（不含）',description='填写带时区的 ISO 时间，修改时需同时填写开始时间')
    focus: str | None = Field(default=None,title='新的关注内容',description='未填写时保留原要求；空字符串清除原关注内容')

    @model_validator(mode='after')
    def range_pair(self):
        if (self.start_at is None)!=(self.end_at is None):
            raise ValueError('Changing a summary range requires both start_at and end_at')
        if self.start_at is not None and self.start_at>=self.end_at:
            raise ValueError('Summary range must satisfy start_at < end_at')
        return self


class SummaryCoverage(BaseModel):
    model_config = ConfigDict(extra='forbid')
    matched_messages: int = Field(ge=0,title='范围内已保存消息')
    participants: int = Field(ge=0,title='范围内参与人数')
    matched_characters: int = Field(ge=0,title='原始字段字符（含协议标记）')
    read_messages: int = Field(default=0,ge=0,title='本工作实际提供给模型的消息文本')
    read_characters: int = Field(default=0,ge=0,title='本工作实际采用的展示文本字符')
    read_event_ids: list[str] = Field(default_factory=list)
    read_result_ranges: dict[str,list[tuple[int,int]]] = Field(default_factory=dict)
    complete: bool = Field(default=False,title='本工作已读完整',description='仅表示实际请求采用的原话文本，不表示图片像素、分析成功或送达')
    analyzed_messages: int = Field(default=0,ge=0,title='已有成功分析的消息（含复用）')
    reused_messages: int = Field(default=0,ge=0,title='复用已保存分析的消息')
    after_rowid: int = Field(default=0,ge=0,title='上个主批次末消息的记录位置')
    batch_result_ids: list[str] = Field(default_factory=list,title='成功批次资料',json_schema_extra={'format':'tool-result-list'})
    pending_input_id: str | None = Field(default=None,title='尚未成功分析的输入',json_schema_extra={'format':'tool-result-id'})
    merged_result_id: str | None = Field(default=None,title='已保存合并结果',json_schema_extra={'format':'tool-result-id'})
    merged_batches: int = Field(default=0,ge=0,title='已经合并的批次')
    report_result_id: str | None = Field(default=None,title='结构化报告',json_schema_extra={'format':'tool-result-id'})
    image_asset_id: str | None = Field(default=None,title='已渲染图片',json_schema_extra={'format':'media-id'})
    image_asset_ids: list[str] = Field(default_factory=list,title='已渲染分页图片',json_schema_extra={'format':'media-id-list'})
    artifact_result_id: str | None = Field(default=None,title='报告交付资料',json_schema_extra={'format':'tool-result-id'})
    analysis_requirements: str | None = Field(default=None,title='本版实际分析要求')
    phase: Literal['analyzing','merging','report_ready','rendering','render_failed','ready'] = Field(default='analyzing',title='当前业务阶段')
    error: str | None = Field(default=None,title='本阶段失败原因')


def summary_goal(request):
    return f'生成本群 [{request.start_at.isoformat()}, {request.end_at.isoformat()}) 的结构化群聊报告。关注：{request.focus}'


def analysis_requirements(request,goal,constraints):
    extra_goal=goal if goal!=summary_goal(request) else ''
    return request.analysis_instructions+'\n本次关注：'+request.focus+'\n用户的额外目标：'+extra_goal+'\n附加要求：'+json.dumps(list(constraints),ensure_ascii=False)


def revise(current, changes, cutoff, now):
    values=current.model_dump()
    if changes.start_at is not None:
        values.update(start_at=changes.start_at,end_at=changes.end_at,snapshot_rowid=cutoff,snapshot_at=now)
    if changes.focus is not None:values['focus']=changes.focus
    parameters=GroupSummaryRange.model_validate(values)
    return PluginWorkRevision(parameters,summary_goal(parameters))


def same_source(left,right):
    return all(getattr(left,key)==getattr(right,key) for key in ('start_at','end_at','snapshot_rowid','bot_actor_id','timezone'))


async def new_progress(store,scene_id,request,previous):
    if (previous and same_source(previous[0],request) and previous[0].focus==request.focus
            and previous[0].analysis_instructions==request.analysis_instructions):return previous[1]
    counts=await store.group_message_statistics(scene_id,start_at=request.start_at.timestamp(),
        end_at=request.end_at.timestamp(),cutoff_rowid=request.snapshot_rowid,bot_actor_id=request.bot_actor_id)
    return SummaryCoverage(matched_messages=counts['message_count'],participants=counts['participant_count'],
        matched_characters=counts['character_count'],complete=counts['message_count']==0)


async def adopt_reads(store,job,request,coverage,presentations):
    read_ids=set(coverage.read_event_ids)
    for span in presentations:
        if span.coordinate_unit!='characters':continue
        result=await store.read_tool_observation(span.result_id,[job['scene_id']])
        if result is None:raise ValueError('Summary presentation has no stored observation')
        if result.coverage!='group_summary_input' or result.status not in {'ok','no_results'}:continue
        lines=result.content.split('\n')
        header=json.loads(lines[0])
        source=GroupSummaryRange.model_validate(header['range'])
        if header['scene_id']!=job['scene_id'] or not same_source(source,request):continue
        merged=[]
        for left,right in sorted([*coverage.read_result_ranges.get(span.result_id,[]),(span.start,span.end)]):
            if merged and left<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],right))
            else:merged.append((left,right))
        coverage.read_result_ranges[span.result_id]=merged
        position=len(lines[0])+1
        for line in lines[1:]:
            record=json.loads(line)
            finish=position+len(line)
            if record['event_id'] not in read_ids and any(left<=position and right>=finish for left,right in merged):
                read_ids.add(record['event_id'])
                coverage.read_characters+=len(record['text'])
            position=finish+1
    coverage.read_event_ids=sorted(read_ids)
    coverage.read_messages=len(read_ids)
    coverage.complete=len(read_ids)==coverage.matched_messages
    return coverage


def finalize(request,coverage,result):
    if result.status not in {'completed','partial'}:return result
    if result.delivery is None:raise ValueError('A report result needs its saved structured artifact and prepared image')
    if coverage.analyzed_messages!=coverage.matched_messages:
        result=result.model_copy(deep=True)
        result.status='partial'
        result.reason=result.reason or 'analysis_incomplete'
        detail=f'范围内有 {coverage.matched_messages} 条已保存消息，已有成功分析 {coverage.analyzed_messages} 条，尚余 {coverage.matched_messages-coverage.analyzed_messages} 条。'
        if detail not in result.unresolved:result.unresolved.append(detail)
    return result


def continuation(job,observation):
    from .service import GroupSummaryService
    return GroupSummaryService.current_continuation(job,observation)


async def execute(context):
    from .analysis import run_report
    return await run_report(context)


def needs_model(job):
    progress=SummaryCoverage.model_validate(job['work_progress'])
    request=GroupSummaryRange.model_validate(job['work_parameters'])
    if not progress.matched_messages:return False
    if progress.analysis_requirements!=analysis_requirements(request,job['goal'],job['constraints']):return True
    if progress.report_result_id and progress.phase in {'report_ready','rendering','render_failed'}:return False
    if progress.merged_result_id and progress.merged_batches==len(progress.batch_result_ids) and progress.phase=='merging':return False
    return progress.phase!='ready' or progress.analyzed_messages<progress.matched_messages


WORK=PluginWorkSpec(operation='group_summary',parameters_model=GroupSummaryRange,
    revision_model=SummaryRevision,progress_model=SummaryCoverage,allow_learning=False,
    allowed_tools=('read_group_chat_window','read_tool_result','read_media'),
    input_cutoff=lambda parameters,current:parameters.snapshot_rowid,revise=revise,
    new_progress=new_progress,adopt_reads=adopt_reads,finalize=finalize,continuation=continuation,
    project_progress=lambda progress:progress.model_dump(mode='json',exclude={'read_event_ids','read_result_ranges'}),
    execute=execute,needs_model=needs_model)
