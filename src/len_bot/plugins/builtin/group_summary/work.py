"""Fixed range, original coverage and completion rules for this plugin."""
from __future__ import annotations

import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from len_bot.plugins.work import PluginWorkSpec, PluginWorkRevision


class GroupSummaryRange(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    start_at: AwareDatetime = Field(title='开始时间（含）')
    end_at: AwareDatetime = Field(title='结束时间（不含）')
    snapshot_rowid: int = Field(ge=0,title='原始资料截点')
    snapshot_at: float = Field(title='数据快照时间',json_schema_extra={'format':'unix-time'})
    bot_actor_id: str = Field(title='排除的机器人账号')
    focus: str = Field(title='关注内容')

    @model_validator(mode='after')
    def ordered(self):
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
    matched_messages: int = Field(ge=0,title='匹配的已保存消息')
    participants: int = Field(ge=0,title='匹配的参与人数')
    matched_characters: int = Field(ge=0,title='匹配的原文字符')
    read_messages: int = Field(ge=0,title='实际完整读过的消息')
    read_characters: int = Field(ge=0,title='实际完整读过的消息字符')
    read_event_ids: list[str]
    read_result_ranges: dict[str,list[tuple[int,int]]]
    complete: bool = Field(title='原话已完整覆盖',description='只表示覆盖本群已保存的匹配原话，不证明总结结论已独立核实，也不表示取得 QQ 全日全部记录')


def summary_goal(request):
    return f'总结本群 [{request.start_at.isoformat()}, {request.end_at.isoformat()}) 的已保存群聊。关注：{request.focus}'


def revise(current, changes, cutoff, now):
    values=current.model_dump()
    if changes.start_at is not None:
        values.update(start_at=changes.start_at,end_at=changes.end_at,snapshot_rowid=cutoff,snapshot_at=now)
    if changes.focus is not None:values['focus']=changes.focus
    parameters=GroupSummaryRange.model_validate(values)
    return PluginWorkRevision(parameters,summary_goal(parameters))


def same_source(left,right):
    return all(getattr(left,key)==getattr(right,key) for key in ('start_at','end_at','snapshot_rowid','bot_actor_id'))


async def new_progress(store,scene_id,request,previous):
    if previous and same_source(previous[0],request):return previous[1]
    counts=await store.group_message_statistics(scene_id,start_at=request.start_at.timestamp(),
        end_at=request.end_at.timestamp(),cutoff_rowid=request.snapshot_rowid,bot_actor_id=request.bot_actor_id)
    return SummaryCoverage(matched_messages=counts['message_count'],participants=counts['participant_count'],
        matched_characters=counts['character_count'],read_messages=0,read_characters=0,
        read_event_ids=[],read_result_ranges={},complete=counts['message_count']==0)


async def adopt_reads(store,job,request,coverage,presentations):
    read_ids=set(coverage.read_event_ids)
    for span in presentations:
        if span.coordinate_unit!='characters':continue
        result=await store.read_tool_observation(span.result_id,[job['scene_id']])
        if result is None:raise ValueError('Summary presentation has no stored observation')
        if result.tool_name!='read_group_chat_window' or result.coverage!='group_summary_window':continue
        if result.status not in {'ok','no_results'}:continue
        lines=result.content.split('\n')
        header=json.loads(lines[0])
        source=GroupSummaryRange.model_validate(header['range'])
        if header['job_id']!=job['id'] or not same_source(source,request):continue
        if not 0<=span.start<=span.end<=len(result.content):
            raise ValueError('Summary presentation exceeds the original observation')
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
    if not coverage.complete:
        result=result.model_copy(deep=True)
        result.status='partial'
        result.reason=result.reason or 'agent_finished_with_unread_messages'
        detail=(f'本群此范围匹配 {coverage.matched_messages} 条已保存人类消息，'
            f'仅完整读取 {coverage.read_messages} 条；剩余原文尚未读取，不是全时段完整总结。')
        if detail not in result.unresolved:result.unresolved.append(detail)
    return result


def continuation(job,observation):
    from .service import GroupSummaryService
    return GroupSummaryService.current_continuation(job,observation)


WORK=PluginWorkSpec(operation='group_summary',parameters_model=GroupSummaryRange,
    revision_model=SummaryRevision,progress_model=SummaryCoverage,allow_learning=False,
    allowed_tools=('read_group_chat_window','read_tool_result','read_media','report_progress','update_work_state','finish_work'),
    input_cutoff=lambda parameters,current:parameters.snapshot_rowid,revise=revise,
    new_progress=new_progress,adopt_reads=adopt_reads,finalize=finalize,continuation=continuation,
    project_progress=lambda progress:progress.model_dump(mode='json',exclude={'read_event_ids','read_result_ranges'}))
