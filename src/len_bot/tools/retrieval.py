"""Scoped local reads for conversation; external read-only capabilities for work."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass
from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from len_bot.cognition.projection import project_event
from len_bot.events.models import Event
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import ToolResult
from len_bot.tools.calculator import CALCULATE_TOOL, calculate
from len_bot.tools.finite_check import FINITE_CHECK_TOOL, finite_check


def tool(name, description, properties, required=()):
    return {'type':'function','function':{'name':name,'description':description,
        'parameters':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}}


S={'type':'string'}
N={'type':'number'}
I={'type':'integer','minimum':1,'maximum':50}
LOCAL_TOOLS=[
    tool('search_messages','按文字查找本群已读截点之前的原话。',{'query':S,'limit':I},['query']),
    tool('read_context','读取消息M前后的本群原话。',{'event_id':S,'before':I,'after':I},['event_id']),
    tool('query_timeline','读取本群指定时间内的消息。',{'start_time':N,'end_time':N,'limit':I},['start_time','end_time']),
    tool('query_person_history','读取本群人物U以前说过的话。',{'actor_id':S,'limit':I},['actor_id']),
    tool('query_memory','按需读取本群认识及其来源；已撤销的认识不是当前事实。',{
        'subject':S,'kind':{'type':'string','enum':['address','preference','relationship','fact','group_norm']},
        'query':S,'include_history':{'type':'boolean'}}),
    tool('query_jobs','读取本群工作的实际版本、资料和进展。',{'job_id':S}),
    tool('read_tool_result','继续阅读已获得的资料R；offset使用上次next_offset。',{'result_id':S,
        'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':12000}},['result_id']),
    tool('search_media','按名称和描述查询本群或运营发布的图片。',{'query':S,'curated_only':{'type':'boolean'}}),
    tool('read_media','装入图片I/P像素和来源；动图只覆盖首帧。',{'asset_id':S},['asset_id']),
]


class MessageRangeArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    message_ref: str = Field(min_length=1)
    offset: int = Field(ge=0)
    limit: int = Field(default=4000, ge=1, le=8000)


class PendingWakeArguments(BaseModel):
    model_config = ConfigDict(extra='forbid')
    after_rowid: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=20)


READ_PENDING_WAKES = tool('read_pending_wakes',
    '分页定位当前场景尚未完整读取的唤醒来源；使用next_after_rowid继续。目录不是原文证据，随后用read_context或read_message_range读取。',
    {'after_rowid':{'type':'integer','minimum':0}, 'limit':{'type':'integer','minimum':1,'maximum':20}})


READ_MESSAGE_RANGE = tool('read_message_range',
    '按字符范围继续读取消息M的原话。使用上次next_offset；片段不代表整条已读，原文全部覆盖后才能作为提案证据。',
    {'message_ref': {'type':'string','minLength':1}, 'offset': {'type':'integer','minimum':0},
     'limit': {'type':'integer','minimum':1,'maximum':8000,'default':4000}}, ['message_ref','offset'])


@dataclass(frozen=True)
class ObservationPage:
    """Stored observation awaiting a budgeted, scene-scoped presentation."""
    name: str
    result: ToolResult
    limit: int
    offset: int = 0


class RetrievalToolkit:
    def __init__(self,event_store,allowed_scopes,default_scene_id,memory_store=None,plugin_host=None,
                 bot_qq='',on_observation=None,checkpoint=None,media_service=None,
                 context=None, *, page_chars: int, max_chars: int, read_concurrency: int,
                 call_context: Callable[[], PluginCallContext]):
        self.event_store=event_store
        # Historical and tool observations are always local. global-safe applies only to media.
        if default_scene_id not in allowed_scopes: raise ValueError('Default scene is outside execution scope')
        self.allowed_scopes=[default_scene_id]
        self.default_scene_id=default_scene_id
        self.memory_store=memory_store
        self.plugin_host=plugin_host
        self.bot_qq=bot_qq
        self.on_observation=on_observation
        self.checkpoint=checkpoint
        self.media_service=media_service
        self.context=context
        self.call_context = call_context
        self.page_chars = page_chars
        self.max_chars = max_chars
        if context and context.refs.scene_id != default_scene_id:
            raise ValueError('Conversation context belongs to another scene')
        self.discovered_tools=set()
        self.result_ids=[]
        self.observations={}
        self.external_attempted=False
        self.unavailable_tools=set()
        self._parallel = asyncio.Semaphore(read_concurrency)

    @property
    def references(self): return self.context.refs if self.context else None
    @property
    def cutoff(self): return self.references.cutoff if self.references else self.call_context().cutoff_rowid

    def get_tool_definitions(self):
        definitions=copy.deepcopy(LOCAL_TOOLS)
        read_result = next(item for item in definitions if item['function']['name'] == 'read_tool_result')
        read_result['function']['parameters']['properties']['limit']['maximum'] = self.max_chars
        if self.context and self.context.pending_wakes():
            definitions.append(copy.deepcopy(READ_PENDING_WAKES))
        if self.context and (self.references.partial_events
                             or set(self.references.events.values()) - self.references.read_events):
            definitions.append(copy.deepcopy(READ_MESSAGE_RANGE))
        if not self.media_service: definitions=[t for t in definitions if t['function']['name'] not in {'search_media','read_media'}]
        if self.call_context().role == 'work':
            definitions.append(copy.deepcopy(CALCULATE_TOOL))
            definitions.append(copy.deepcopy(FINITE_CHECK_TOOL))
            if self.media_service:
                definitions.append(tool('read_web_media','查看公开网页中的原图或PDF的一页，直接向模型提供像素。使用已知图片/PDF链接；不读取HTML页面。PDF页码从1开始，省略默认第1页。',
                    {'url':S,'page':{'type':'integer','minimum':1,'maximum':100}},['url']))
        plugin_tools = self.plugin_host.get_tool_definitions(self.call_context(), kind='read') if self.plugin_host else []
        if plugin_tools:
            definitions.append(tool('tool_search','按名称或描述发现当前群和当前职责可用的读取工具。',{'query':S},['query']))
        for definition in plugin_tools:
            name = definition['function']['name']
            capabilities = self.plugin_host.tool_capabilities(name)
            if name not in self.unavailable_tools and (not capabilities['deferred'] or name in self.discovered_tools):
                definitions.append(copy.deepcopy(definition))
        return definitions

    def is_read_only(self,name):
        if name in {t['function']['name'] for t in LOCAL_TOOLS}|{'calculate','finite_check','read_web_media','tool_search','read_message_range','read_pending_wakes'}: return True
        return bool(self.plugin_host and self.plugin_host.has_tool(name, self.call_context())
                    and self.plugin_host.tool_capabilities(name)['kind'] == 'read')

    async def import_results(self,result_ids):
        for result_id in result_ids:
            result=await self.event_store.read_tool_observation(result_id,self.allowed_scopes)
            if result is None:raise ValueError('Transferred result does not belong to this scene')
            if result_id not in self.result_ids:self.result_ids.append(result_id)
            self.observations[result_id]=result
            if result.evidence_kind == 'external':
                self.external_attempted=True

    def _resolve_arguments(self,name,args):
        if not self.references:return args
        refs=self.references
        for key,resolve in [('event_id',refs.locate_event),('message_ref',refs.locate_event),('actor_id',refs.actor_id),('asset_id',refs.media_id),
                            ('subject',refs.actor_id),('result_id',refs.result_id)]:
            if args.get(key):args[key]=resolve(args[key])
        if args.get('job_id'):args['job_id']=refs.job(args['job_id'])['id']
        return args

    async def execute_result(self, name, arguments) -> ToolResult:
        page = await self.execute_observation(name, arguments)
        return await self._present(page.name, page.result, page.offset, page.limit)

    async def execute_observation(self, name, arguments) -> ObservationPage:
        """Fetch and persist in parallel; references are granted only when presented."""
        if name == 'read_pending_wakes':
            try:
                arguments = PendingWakeArguments.model_validate(arguments).model_dump()
            except ValidationError as error:
                return ObservationPage(name, ToolResult.failure(str(error), 'invalid_arguments'), limit=self.page_chars)
        definitions={t['function']['name']:t for t in self.get_tool_definitions()}
        if name not in definitions:return ObservationPage(name, ToolResult.failure('本入口未开放此工具','capability_denied'), limit=self.page_chars)
        if name == 'read_message_range':
            try:
                arguments = MessageRangeArguments.model_validate(arguments).model_dump()
            except ValidationError as error:
                return ObservationPage(name, ToolResult.failure(str(error), 'invalid_arguments'), limit=self.page_chars)
        try:args=self._resolve_arguments(name,dict(arguments))
        except ValueError as error:return ObservationPage(name, ToolResult.failure(str(error),'invalid_reference'), limit=self.page_chars)
        if name=='read_tool_result':
            offset, limit = int(args.get('offset', 0)), int(args.get('limit', self.page_chars))
            if offset < 0 or not 1 <= limit <= self.max_chars:
                return ObservationPage(name, ToolResult.failure(
                    f'offset must be nonnegative; limit must be 1..{self.max_chars}', 'invalid_arguments'), limit=self.page_chars)
            result=await self.event_store.read_tool_observation(args.get('result_id',''),self.allowed_scopes)
            if result is None:return ObservationPage(name, ToolResult.failure('资料不存在或不属于本群','not_found'), limit=self.page_chars)
            call=await self.event_store.tool_observation_call(result.result_id,self.default_scene_id)
            return ObservationPage(call[0] if call else '',result,offset=offset,limit=limit)
        if name=='tool_search':
            query=str(args.get('query','')).casefold().strip()
            if not query:return ObservationPage(name, ToolResult.failure('query不能为空','invalid_arguments'), limit=self.page_chars)
            matches=[t['function']['name'] for t in self.plugin_host.get_tool_definitions(self.call_context(), kind='read') if
                self.is_read_only(t['function']['name']) and t['function']['name'] not in self.unavailable_tools
                and query in (t['function']['name']+' '+t['function']['description']).casefold()] if self.plugin_host else []
            self.discovered_tools.update(matches[:8])
            return ObservationPage(name, ToolResult(status='ok' if matches else 'no_results',content=json.dumps(matches[:8],ensure_ascii=False),coverage='tool_catalog'), limit=self.page_chars)
        if self.checkpoint:
            await self.checkpoint('before_tool', {'scene_id':self.default_scene_id,'name':name,'arguments':args})
        start = time.monotonic()
        media_files = []
        invocation = self.call_context()
        plugin_tool = bool(self.plugin_host and self.plugin_host.has_tool(name, invocation))
        async with self._parallel:
            if plugin_tool:
                result = await self.plugin_host.execute_tool(name, args, invocation)
            else:
                try:
                    if name == 'read_web_media':
                        self.external_attempted = True
                        result, media_files = await self.media_service.read_web_media(args.get('url', ''), args.get('page'))
                    else:
                        result = await self._execute_raw(name, args)
                except Exception as error:
                    result = ToolResult.failure(str(error), type(error).__name__)
        result.duration_ms = round((time.monotonic()-start)*1000, 2)
        if result.evidence_kind == 'external':
            self.external_attempted = True
        if not plugin_tool and result.evidence_kind == 'unknown':
            result.evidence_kind = 'retrieval'
        if name == 'web_search' and result.status in {'error', 'unsupported'} and result.error_code != 'invalid_arguments':
            self.external_attempted = True
            self.unavailable_tools.add(name)
            result.content += '\n本次工作的搜索服务不可用，已停止继续调用；可读取已有官方链接，未核实部分写入unresolved。'
        result, event = await self.event_store.save_tool_observation(self.default_scene_id, name, args, result,
            background_work=invocation.role == 'work', media_files=media_files)
        self.result_ids.append(result.result_id)
        self.observations[result.result_id] = result
        if self.on_observation:
            await self.on_observation(event)
        if self.checkpoint:
            await self.checkpoint('after_tool', {'scene_id':self.default_scene_id,'name':name,'result':result.model_dump()})
        page_chars = self.page_chars
        if name == 'read_group_chat_window':
            plugin = self.plugin_host.get_plugin('group_summary')
            page_chars = plugin.config.page_chars
        return ObservationPage(name, result, limit=page_chars)

    def validate_conclusion_sources(self, result_ids, unresolved):
        """Validate evidence availability, not the semantic truth of a conclusion."""
        if not set(result_ids).issubset(self.result_ids):
            raise ValueError('Result references resources this work has not observed')
        if unresolved:
            return
        sources=[self.observations[ident] for ident in result_ids]
        if any(item.status not in {'ok','partial'} for item in sources):
            raise ValueError('完成结论引用了失败、不可用或空结果。请补充有效依据，或把尚未核实的要求写入unresolved。')
        if self.external_attempted and not any(
            (item.evidence_kind=='external' and item.sources and item.coverage not in {'search_snippets','pdf_text_empty'})
            or item.coverage in {'arithmetic','finite_enumeration'} for item in sources
        ):
            raise ValueError('外部查询尚无成功读取的来源；搜索摘要、本地对话和自己的确认不能证明外部事实。请读取相关原始来源，或将未核实部分写入unresolved，结论只保留已验证内容。')

    def observation_locator(self, page):
        if not page.result.result_id:
            return page.result.page(page.offset, page.limit)
        return ToolResult(status='partial' if page.result.status in {'ok','partial'} else page.result.status,
            result_id=self.references.register_result(page.result.result_id) if self.references else page.result.result_id,
            content='本轮输入额度有限，正文尚未装入；用read_tool_result按result_id和next_offset继续读取。',
            truncated=True, next_offset=page.offset,
            coverage='result_locator; content not read', evidence_kind=page.result.evidence_kind,
            error_code=page.result.error_code, fetched_at=page.result.fetched_at,
            observation_event_id=page.result.observation_event_id, cached=page.result.cached)

    async def _present(self, name, result, offset, limit):
        if offset < 0 or not 1 <= limit <= self.max_chars:
            raise ValueError(f'offset must be nonnegative; limit must be 1..{self.max_chars}')
        if not self.context:return result.page(offset,limit)
        refs=self.references;shown=result.model_copy(deep=True)
        if shown.result_id:shown.result_id=refs.register_result(shown.result_id)
        if name == 'read_pending_wakes':
            if shown.status not in {'ok','partial'}:return shown
            page = json.loads(shown.content)
            ids = {item['event_id'] for item in page['items']}
            events = await self.event_store.events_by_ids(self.default_scene_id,ids,self.cutoff)
            if {event.id for event in events} != ids:
                return ToolResult.failure('目录原始来源不属于本场景或超出本轮截点','invalid_source')
            shown.content = json.dumps(self.context.project_pending_page(page),ensure_ascii=False)
            return shown
        if name == 'read_message_range':
            if shown.status not in {'ok', 'partial'}:
                return shown
            if offset:
                return ToolResult.failure('此资料保存一个原话片段；继续原文请用read_message_range(message_ref, next_offset)。', 'invalid_arguments')
            item = json.loads(shown.content)
            events = await self.event_store.events_by_ids(self.default_scene_id, [item['event_id']], self.cutoff)
            if not events:
                return ToolResult.failure('原话不属于本场景或超出本轮截点', 'not_found')
            event = events[0]
            start, end, total = item['range']
            if total != len(event.raw_text) or item['text'] != event.raw_text[start:end]:
                return ToolResult.failure('原话片段与不可变来源不一致', 'invalid_source_range')
            end = min(end, start + limit)
            refs.register_event_range(event, start, end, total)
            shown.content = json.dumps({'message_ref':refs.register_event_locator(event.id),
                'actor':refs.register_actor(event.actor_id), 'range':[start,end,total],
                'text':event.raw_text[start:end], 'next_offset':end if end < total else None}, ensure_ascii=False)
            shown.truncated = end < total
            shown.next_offset = end if end < total else None
            return shown
        local={'search_messages','read_context','query_timeline','query_person_history','search_media','query_memory','query_jobs'}
        if name not in local or shown.status not in {'ok', 'partial', 'no_results'}:
            return shown.page(offset,limit)
        # Work history observations contain projected text; conversation
        # observations contain event records so presentation can grant refs.
        try:
            data = json.loads(shown.content)
        except ValueError:
            return shown.page(offset, limit)
        if not isinstance(data, list):
            return shown.page(offset, limit)
        history=name in {'search_messages','read_context','query_timeline','query_person_history'}

        def project(raw):
            item=copy.deepcopy(raw);refs=self.references
            if history:
                event=Event.model_validate(item)
                if event.metadata['_rowid']>refs.cutoff:raise ValueError('Retrieved message exceeds read cutoff')
                return self.context.event_message(event)['content']
            if name=='search_media':
                return {'asset_id':refs.register_media(item.pop('asset_id')),**item}
            if name=='query_memory':
                editable=item['status']=='active' and (item['expires_at'] is None or item['expires_at']>self.context.runtime.clock())
                ref=refs.register_memory(item.pop('id'),editable=editable)
                item['subject']=refs.register_actor(item['subject'])
                item['evidence']=[refs.register_event_locator(e) for e in item['evidence']]
                item['revision_evidence']=[refs.register_event_locator(e) for e in item['revision_evidence']]
                return {'ref':ref,**item}
            ref=refs.register_job(item)
            item.pop('id')
            item['result_ids']=[refs.register_result(r) for r in item['result_ids']]
            return {'ref':ref,**item}

        selected=[]
        index=min(offset,len(data))
        while index<len(data):
            original=self.context.refs
            self.context.refs=copy.deepcopy(original)
            try:candidate=project(data[index])
            finally:self.context.refs=original
            candidate_text='\n'.join([*selected,candidate]) if history else json.dumps([*selected,candidate],ensure_ascii=False)
            if len(candidate_text)>limit:
                if not selected:
                    if not history:
                        return ToolResult.failure(f'limit太小，无法完整展示此条记录和引用；请将limit调大，至少需要{len(candidate_text)}字符。','page_too_small')
                    event = Event.model_validate(data[index])
                    total = len(event.raw_text)
                    refs.register_event_range(event, 0, 0, total)
                    locator = {'message_ref':refs.register_event_locator(event.id),
                        'actor':refs.register_actor(event.actor_id), 'range':[0,0,total], 'next_offset':0,
                        'note':'本页只提供原话位置；请用read_message_range按字符范围读取，尚未授权整条证据。'}
                    quote = event.metadata.get('quote_context')
                    if quote and not quote.get('missing') and quote['rowid'] <= refs.cutoff:
                        locator['quoted_message'] = {'message_ref':refs.register_event_locator(quote['event_id']),
                            'actor':refs.register_actor(quote['actor_id']), 'total':len(quote['text'])}
                    shown.content = json.dumps(locator, ensure_ascii=False)
                    shown.truncated, shown.next_offset = True, 0
                    shown.coverage = 'original_message_locator; use read_message_range(message_ref, next_offset)'
                    return shown
                break
            selected.append(project(data[index]))
            index+=1
        shown.content='\n'.join(selected) if history else json.dumps(selected,ensure_ascii=False)
        shown.truncated=index<len(data)
        shown.next_offset=index if shown.truncated else None
        shown.coverage='record_page; offset is the next record index' if shown.truncated or offset else shown.coverage
        return shown

    async def _execute_raw(self, name, args) -> ToolResult:
        store=self.event_store;scopes=self.allowed_scopes
        if name == 'read_pending_wakes':
            if not self.context:
                return ToolResult.failure('Pending sources require a conversation snapshot','invalid_context')
            page = self.context.pending_wake_page(**args)
            return ToolResult(content=json.dumps(page,ensure_ascii=False),
                coverage='pending_wake_snapshot; as_of_rowid is its snapshot and next_after_rowid the directory cursor; locators are not evidence', evidence_kind='retrieval')
        if name == 'read_message_range':
            events = await store.events_by_ids(self.default_scene_id, [args['message_ref']], self.cutoff)
            if not events:
                return ToolResult.failure('原话不属于本场景或超出本轮截点', 'not_found')
            event = events[0]
            start, total = args['offset'], len(event.raw_text)
            if start > total:
                return ToolResult.failure('offset超出原话长度', 'invalid_arguments')
            end = min(total, start + args['limit'])
            return ToolResult(status='partial' if start or end < total else 'ok',
                content=json.dumps({'event_id':event.id, 'scene_id':event.scene_id,
                    'actor_id':event.actor_id, 'rowid':event.metadata['_rowid'],
                    'range':[start,end,total], 'text':event.raw_text[start:end]}, ensure_ascii=False),
                truncated=end < total, next_offset=end if end < total else None,
                coverage='original_message_range; next_offset is a raw-text character offset for read_message_range',
                evidence_kind='retrieval')
        if name=='read_media':return await self.media_service.read_media(args.get('asset_id',''),self.default_scene_id)
        if name=='search_media':
            rows=await store.list_media([self.default_scene_id,'global-safe'],query=str(args.get('query','')),curated_only=bool(args.get('curated_only',True)))
            records = [{'asset_id':x['id'],**{k:x[k] for k in ('scope','source_event_id','description','tags')}} for x in rows]
            return ToolResult(status='ok' if records else 'no_results', content=json.dumps(records, ensure_ascii=False),
                              coverage='media_catalog', evidence_kind='retrieval')
        if name=='query_jobs':
            if args.get('job_id'):
                job = await store.get_job(args['job_id'], self.default_scene_id)
                jobs = [job] if job else []
            else:
                jobs = await store.list_jobs(self.default_scene_id)
            for job in jobs:
                if job['summary_coverage'] is not None:
                    job['summary_coverage'] = {key:value for key,value in job['summary_coverage'].items()
                                               if key not in {'read_result_ranges', 'read_event_ids'}}
            return ToolResult(status='ok' if jobs else 'no_results', content=json.dumps(jobs, ensure_ascii=False),
                              coverage='current_jobs', evidence_kind='retrieval')
        if name=='calculate':return calculate(args.get('expression',''))
        if name=='finite_check':return await asyncio.to_thread(finite_check,**args)
        rows=None;limit=max(1,min(int(args.get('limit',15)),50))
        if name=='search_messages':rows=await store.search_messages(str(args.get('query','')),scopes,limit,through_rowid=self.cutoff)
        elif name=='read_context':rows=await store.read_context(str(args.get('event_id','')),int(args.get('before',3)),int(args.get('after',3)),scopes,through_rowid=self.cutoff)
        elif name=='query_timeline':rows=await store.query_timeline(self.default_scene_id,float(args['start_time']),float(args['end_time']),scopes,limit,through_rowid=self.cutoff)
        elif name=='query_person_history':rows=await store.query_person_history(str(args.get('actor_id','')),scopes,limit,through_rowid=self.cutoff)
        elif name=='query_memory':
            if not self.memory_store:return ToolResult(status='unsupported',content='未配置认识账本')
            memories=await self.memory_store.query_memories(scopes,subject=args.get('subject'),kind=args.get('kind'),query=args.get('query'),include_superseded=bool(args.get('include_history',False)))
            return ToolResult(status='ok' if memories else 'no_results',
                              content=json.dumps([m.model_dump(mode='json') for m in memories], ensure_ascii=False),
                              coverage='memory_ledger', evidence_kind='retrieval')
        if rows is not None:
            enriched=await store.project_reply_context(self.default_scene_id,[Event.model_validate(r) for r in rows],through_rowid=self.cutoff)
            if self.context:
                content = json.dumps([event.model_dump(mode='json') for event in enriched], ensure_ascii=False)
            else:
                content = '\n'.join(project_event(event, self.bot_qq) for event in enriched)
            return ToolResult(status='ok' if enriched else 'no_results', content=content,
                              coverage='original_messages', evidence_kind='retrieval')
        return ToolResult.failure('未知工具','not_found')
