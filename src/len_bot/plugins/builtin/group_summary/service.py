"""Read fixed current-group records and scoped, saved report resources."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from len_bot.cognition.projection import project_onebot_text
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import ToolNextCall, ToolResult, ToolSource

from .config import GroupSummaryConfig
from .report import BatchAnalysis, ReportArtifact, SingleGroupReport, SourceMessage
from .work import GroupSummaryRange, same_source


class WindowCursor(BaseModel):
    model_config=ConfigDict(extra='forbid')
    job_id: str
    revision: int = Field(ge=1)
    index: int = Field(ge=0)


class GroupSummaryService:
    def __init__(self,event_store,config: GroupSummaryConfig):
        self.event_store,self.config=event_store,config

    async def source_messages(self,scene_id,request):
        events=[]
        after=0
        while True:
            page=await self.event_store.group_message_window(scene_id,start_at=request.start_at.timestamp(),
                end_at=request.end_at.timestamp(),cutoff_rowid=request.snapshot_rowid,
                bot_actor_id=request.bot_actor_id,after_rowid=after,limit=self.config.page_messages)
            if not page:break
            events.extend(page)
            after=page[-1].metadata['_rowid']
            if len(page)<self.config.page_messages:break
        by_message={str(event.payload['message_id']):event.id for event in events if event.payload.get('message_id') is not None}
        records=[]
        for event in events:
            sender=event.payload.get('sender') or {}
            records.append(SourceMessage(ref=f'M{event.metadata["_rowid"]}',event_id=event.id,
                rowid=event.metadata['_rowid'],actor_id=event.actor_id,
                display_name=sender.get('card') or sender.get('nickname') or event.actor_id,
                sent_at=datetime.fromtimestamp(event.timestamp,ZoneInfo(request.timezone)),text=project_onebot_text(event.raw_text),
                reply_to_event_id=by_message.get(str(event.payload.get('reply_to_message_id')))))
        return sorted(records,key=lambda record:(record.sent_at,record.rowid))

    @staticmethod
    def input_result(scene_id,job_id,revision,request,records,*,next_cursor=None):
        header={'scene_id':scene_id,'job_id':job_id,'job_revision':revision,
            'range':request.model_dump(mode='json'),'next_cursor':next_cursor,
            'scope':'仅当前范围内已保存的非Bot群消息；CQ媒体只投影为类型标记，不表示已读像素。context_only=true只供理解本批引用。'}
        content='\n'.join([json.dumps(header,ensure_ascii=False),
            *(record.model_dump_json(exclude_none=True) for record in records)])
        return ToolResult(status='ok' if records else 'no_results',content=content,
            evidence_kind='retrieval',coverage='group_summary_input',
            sources=[ToolSource(title=f'{scene_id} 已保存的固定范围原话')],
            source_next_call=ToolNextCall(name='read_group_chat_window',arguments={'cursor':next_cursor}) if next_cursor else None)

    @staticmethod
    def parse_input(result):
        lines=result.content.split('\n')
        header=json.loads(lines[0])
        GroupSummaryRange.model_validate(header['range'])
        return header,[SourceMessage.model_validate_json(line) for line in lines[1:]]

    async def saved_resources(self,call,coverage):
        before=None
        while True:
            rows=await self.event_store.plugin_observations(call.scene_id,call.origin.plugin_id,
                call.origin.plugin_version,coverage,limit=self.config.page_messages,before_rowid=before)
            if not rows:return
            for _,result in rows:yield result
            before=rows[-1][0]
            if len(rows)<self.config.page_messages:return

    async def reusable_batches(self,call,request,instructions):
        batches={}
        async for resource in self.saved_resources(call,'group_summary_batch_analysis'):
            batch=BatchAnalysis.model_validate_json(resource.content)
            if batch.focus!=request.focus or batch.analysis_instructions!=instructions:continue
            if not batch.source_event_ids:raise ValueError('Saved batch has no primary source identity')
            batches.setdefault(batch.source_event_ids[0],[]).append((resource,batch))
        return batches

    async def read_window(self,cursor: str | None,call: PluginCallContext):
        if call.role!='work' or not call.job_id:raise ValueError('群原话窗口只在已提交的总结工作中读取')
        job=await self.event_store.get_job(call.job_id,call.scene_id)
        if not job or job['work_operation']!='group_summary' or job['status']!='processing':
            raise ValueError('当前场景没有对应的运行中总结工作')
        request=GroupSummaryRange.model_validate(job['work_parameters'])
        if call.cutoff_rowid!=request.snapshot_rowid or call.requester_qq_uid!=job['requester_qq_uid']:
            raise ValueError('总结读取上下文与已保存的请求者或快照不一致')
        index=0
        if cursor is not None:
            position=WindowCursor.model_validate_json(cursor)
            if position.job_id!=job['id'] or position.revision!=job['revision']:
                raise ValueError('游标不属于当前工作版本；使用当前版本提供的续页位置')
            index=position.index
        records=await self.source_messages(call.scene_id,request)
        if index>len(records):raise ValueError('Source cursor exceeds its fixed message range')
        selected=records[index:index+self.config.page_messages]
        next_cursor=WindowCursor(job_id=job['id'],revision=job['revision'],index=index+len(selected)).model_dump_json() if index+len(selected)<len(records) else None
        return self.input_result(call.scene_id,job['id'],job['revision'],request,selected,next_cursor=next_cursor)

    @staticmethod
    def current_continuation(job,observation):
        if observation.coverage!='group_summary_input' or observation.source_next_call is None:return None
        if observation.source_next_call.name!='read_group_chat_window':raise ValueError('Saved report input has a foreign continuation')
        header,_=GroupSummaryService.parse_input(observation)
        if header['job_id']!=job['id'] or not same_source(GroupSummaryRange.model_validate(header['range']),GroupSummaryRange.model_validate(job['work_parameters'])):return None
        cursor=WindowCursor.model_validate_json(observation.source_next_call.arguments['cursor'])
        if cursor.job_id!=job['id'] or cursor.revision!=header['job_revision']:raise ValueError('Saved cursor does not match its original input')
        return ToolNextCall(name='read_group_chat_window',arguments={'cursor':WindowCursor(
            job_id=job['id'],revision=job['revision'],index=cursor.index).model_dump_json()})

    async def read_report(self,call,start_at,end_at,revision=None):
        async for resource in self.saved_resources(call,'group_summary_artifact'):
            artifact=ReportArtifact.model_validate_json(resource.content)
            if artifact.start_at!=start_at or artifact.end_at!=end_at:continue
            if revision is not None and artifact.job_revision!=revision:continue
            observed=await self.event_store.read_tool_observation(artifact.report_result_id,[call.scene_id])
            if observed is None:raise ValueError('Saved report artifact has no structured result')
            report=SingleGroupReport.model_validate_json(observed.content)
            asset_ids=artifact.image_asset_ids or [artifact.image_asset_id]
            available=[]
            for asset_id in asset_ids:
                if await self.event_store.get_media(asset_id,[call.scene_id]):
                    available.append(asset_id)
            job=await self.event_store.get_job(artifact.job_id,call.scene_id)
            return ToolResult(status='ok' if len(available)==len(asset_ids) else 'partial',coverage='saved_group_report',evidence_kind='model',
                attachments=available,sources=observed.sources,
                content=json.dumps({'artifact_result_id':resource.result_id,'report_result_id':observed.result_id,
                    'artifact':artifact.model_dump(mode='json'),'report':report.model_dump(mode='json'),'image_available':bool(available),
                    'delivery_status':job['status'] if job and job['revision']==artifact.job_revision else 'see_historical_receipts',
                    'note':'这是保存的派生报告；读取不会重新分析、渲染或发送。统计和引用由程序提取，话题与点评来自当时模型分析。'},ensure_ascii=False))
        return ToolResult(status='no_results',coverage='saved_group_report',evidence_kind='retrieval',
            content='当前群、该精确范围与所选版本没有已生成的报告图片；此读取不会新建工作。')
