"""Single-group report data; counts, identities and quotes are program-owned."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .work import GroupSummaryRange


class ReportData(BaseModel):
    model_config=ConfigDict(extra='forbid')


class SourceMessage(ReportData):
    ref: str
    event_id: str
    rowid: int
    actor_id: str
    display_name: str
    sent_at: AwareDatetime
    text: str
    reply_to_event_id: str | None = None
    context_only: bool = False


class TopicChoice(ReportData):
    title: str = Field(min_length=1,max_length=70)
    summary: str = Field(min_length=1,max_length=500)
    sources: list[str] = Field(min_length=1,max_length=12,description='本批正文中的消息ref；引用上下文不能代替本批来源')


class QuoteChoice(ReportData):
    source: str = Field(description='本批一条消息的ref；程序从该消息提取引语')
    start: int = Field(default=0,ge=0,description='展示文本中的字符起点，包含')
    end: int | None = Field(default=None,ge=1,description='字符终点，不包含；null取至原文结尾')
    comment: str = Field(max_length=120)


class BatchChoices(ReportData):
    topics: list[TopicChoice] = Field(max_length=6)
    quotes: list[QuoteChoice] = Field(max_length=3)
    comment: str = Field(max_length=180)
    unresolved: list[str] = Field(max_length=8)


class ReportTopic(ReportData):
    id: str
    title: str
    summary: str
    source_event_ids: list[str]
    participant_ids: list[str]


class ReportQuote(ReportData):
    id: str
    event_id: str
    actor_id: str
    display_name: str
    sent_at: AwareDatetime
    start: int
    end: int
    text: str
    comment: str


class BatchAnalysis(ReportData):
    input_result_id: str
    source_event_ids: list[str]
    after_rowid: int
    focus: str
    analysis_instructions: str
    topics: list[ReportTopic]
    quotes: list[ReportQuote]
    comment: str
    unresolved: list[str]


class MergedTopicChoice(ReportData):
    title: str = Field(min_length=1,max_length=70)
    summary: str = Field(min_length=1,max_length=700)
    topic_ids: list[str] = Field(min_length=1,description='合并所依据的候选话题ID，必须来自本次输入')


class ReportChoices(ReportData):
    topics: list[MergedTopicChoice] = Field(max_length=12)
    quote_ids: list[str] = Field(max_length=8,description='选用已有金句ID；不重写正文和作者')
    comment: str = Field(max_length=240)
    unresolved: list[str] = Field(max_length=12)


class MergedAnalysis(ReportData):
    batch_result_ids: list[str]
    topics: list[ReportTopic]
    quotes: list[ReportQuote]
    comment: str
    unresolved: list[str]


class ActiveMember(ReportData):
    actor_id: str
    display_name: str
    messages: int


class ActivityHour(ReportData):
    start_at: str
    messages: int


class ReportStatistics(ReportData):
    messages: int
    participants: int
    characters: int
    activity: list[ActivityHour]
    active_members: list[ActiveMember]


class ReportCoverage(ReportData):
    read_messages: int
    analyzed_messages: int
    reused_messages: int
    unfinished_messages: int
    complete: bool


class SingleGroupReport(ReportData):
    scene_id: str
    job_id: str
    job_revision: int
    range: GroupSummaryRange
    analysis_requirements: str
    generated_at: float
    statistics: ReportStatistics
    coverage: ReportCoverage
    batch_result_ids: list[str]
    topics: list[ReportTopic]
    quotes: list[ReportQuote]
    comment: str
    unresolved: list[str]


class ReportArtifact(ReportData):
    scene_id: str
    job_id: str
    job_revision: int
    report_result_id: str
    image_asset_id: str
    start_at: AwareDatetime
    end_at: AwareDatetime
    generated_at: float


def statistics(messages: list[SourceMessage], timezone: str) -> ReportStatistics:
    zone=ZoneInfo(timezone)
    members={}
    hours=Counter()
    for message in messages:
        current=members.setdefault(message.actor_id,ActiveMember(actor_id=message.actor_id,
            display_name=message.display_name,messages=0))
        current.display_name=message.display_name
        current.messages+=1
        hour=message.sent_at.astimezone(zone).replace(minute=0,second=0,microsecond=0).isoformat()
        hours[hour]+=1
    return ReportStatistics(messages=len(messages),participants=len(members),
        characters=sum(len(message.text) for message in messages),
        activity=[ActivityHour(start_at=hour,messages=count) for hour,count in sorted(hours.items())],
        active_members=sorted(members.values(),key=lambda item:(-item.messages,item.actor_id)))


def adopt_batch(choices: BatchChoices,source,records: list[SourceMessage],request: GroupSummaryRange,instructions: str) -> BatchAnalysis:
    targets={record.ref:record for record in records if not record.context_only}
    topics=[]
    for index,choice in enumerate(choices.topics):
        if set(choice.sources)-targets.keys():raise ValueError('Batch topic cites a message outside its supplied primary range')
        topics.append(ReportTopic(id=f'{source.result_id}:topic:{index}',title=choice.title,summary=choice.summary,
            source_event_ids=list(dict.fromkeys(targets[ref].event_id for ref in choice.sources)),
            participant_ids=list(dict.fromkeys(targets[ref].actor_id for ref in choice.sources))))
    quotes=[]
    for choice in choices.quotes:
        if choice.source not in targets:raise ValueError('Batch quote cites a message outside its supplied primary range')
        record=targets[choice.source]
        end=len(record.text) if choice.end is None else choice.end
        if not 0<=choice.start<end<=len(record.text) or end-choice.start>400:
            raise ValueError('Quote range must identify at most 400 actual source characters')
        quotes.append(ReportQuote(id=f'{record.event_id}:{choice.start}:{end}',event_id=record.event_id,
            actor_id=record.actor_id,display_name=record.display_name,sent_at=record.sent_at,start=choice.start,end=end,
            text=record.text[choice.start:end],comment=choice.comment))
    return BatchAnalysis(input_result_id=source.result_id,source_event_ids=[record.event_id for record in targets.values()],
        after_rowid=list(targets.values())[-1].rowid,focus=request.focus,
        analysis_instructions=instructions,topics=topics,quotes=quotes,
        comment=choices.comment,unresolved=choices.unresolved)


def adopt_merge(choices: ReportChoices,topics: list[ReportTopic],quotes: list[ReportQuote],batch_ids) -> MergedAnalysis:
    available={topic.id:topic for topic in topics}
    chosen=[]
    for index,topic in enumerate(choices.topics):
        if set(topic.topic_ids)-available.keys():raise ValueError('Merged topic cites an unknown candidate')
        sources=list(dict.fromkeys(event for ident in topic.topic_ids for event in available[ident].source_event_ids))
        people=list(dict.fromkeys(actor for ident in topic.topic_ids for actor in available[ident].participant_ids))
        chosen.append(ReportTopic(id=f'merged:{len(batch_ids)}:{index}',title=topic.title,summary=topic.summary,
            source_event_ids=sources,participant_ids=people))
    quote_map={quote.id:quote for quote in quotes}
    if set(choices.quote_ids)-quote_map.keys():raise ValueError('Merged report selected an unknown quote')
    if len(choices.quote_ids)!=len(set(choices.quote_ids)):raise ValueError('Merged report repeats a quote identity')
    return MergedAnalysis(batch_result_ids=list(batch_ids),topics=chosen,
        quotes=[quote_map[ident] for ident in choices.quote_ids],comment=choices.comment,unresolved=choices.unresolved)
