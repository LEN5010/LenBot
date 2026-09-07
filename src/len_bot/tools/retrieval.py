"""Scoped local reads for conversation; external read-only capabilities for work."""
from __future__ import annotations

import asyncio
import copy
import json
import time

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from len_bot.cognition.projection import project_event
from len_bot.events.models import Event
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


READ_MESSAGE_RANGE = tool('read_message_range',
    '按字符范围继续读取消息M的原话。使用上次next_offset；片段不代表整条已读，原文全部覆盖后才能作为提案证据。',
    {'message_ref': {'type':'string','minLength':1}, 'offset': {'type':'integer','minimum':0},
     'limit': {'type':'integer','minimum':1,'maximum':8000,'default':4000}}, ['message_ref','offset'])


class RetrievalToolkit:
    def __init__(self,event_store,allowed_scopes,default_scene_id,memory_store=None,plugin_host=None,
                 bot_qq='',on_observation=None,read_only_only=False,checkpoint=None,media_service=None,
                 context=None):
        self.event_store=event_store
        # Historical and tool observations are always local. global-safe applies only to media.
        if default_scene_id not in allowed_scopes: raise ValueError('Default scene is outside execution scope')
        self.allowed_scopes=[default_scene_id]
        self.default_scene_id=default_scene_id
        self.memory_store=memory_store
        self.plugin_host=plugin_host
        self.bot_qq=bot_qq
        self.on_observation=on_observation
        self.read_only_only=read_only_only
        self.checkpoint=checkpoint
        self.media_service=media_service
        self.context=context
        self.discovered_tools=set()
        self.result_ids=[]
        self.observations={}
        self.external_attempted=False
        self.unavailable_tools=set()
        self._cache={}
        self._call_locks={}
        self._parallel=asyncio.Semaphore(3)

    @property
    def references(self): return self.context.refs if self.context else None
    @property
    def cutoff(self): return self.references.cutoff if self.references else None

    def get_tool_definitions(self):
        definitions=copy.deepcopy(LOCAL_TOOLS)
        if self.context and (self.references.partial_events
                             or set(self.references.events.values()) - self.references.read_events):
            definitions.append(copy.deepcopy(READ_MESSAGE_RANGE))
        if not self.media_service: definitions=[t for t in definitions if t['function']['name'] not in {'search_media','read_media'}]
        if self.read_only_only:
            definitions.append(copy.deepcopy(CALCULATE_TOOL))
            definitions.append(copy.deepcopy(FINITE_CHECK_TOOL))
            if self.media_service:
                definitions.append(tool('read_web_media','查看公开网页中的原图或PDF的一页，直接向模型提供像素。使用已知图片/PDF链接；不读取HTML页面。PDF页码从1开始，省略默认第1页。',
                    {'url':S,'page':{'type':'integer','minimum':1,'maximum':100}},['url']))
            definitions.append(tool('tool_search','按名称或描述发现可用的外部只读工具。',{'query':S},['query']))
            for definition in self.plugin_host.get_tool_definitions() if self.plugin_host else []:
                name=definition['function']['name'];caps=self.plugin_host.tool_capabilities(name)
                if name not in self.unavailable_tools and caps['read_only'] and (not caps['deferred'] or name in self.discovered_tools):
                    definition=copy.deepcopy(definition)
                    definition['function']['parameters'].setdefault('properties',{})['refresh']={
                        'type':'boolean','description':'重新获取，不复用已有资料'}
                    definitions.append(definition)
        return definitions

    def is_read_only(self,name):
        if name in {t['function']['name'] for t in LOCAL_TOOLS}|{'calculate','finite_check','read_web_media','tool_search','read_message_range'}: return True
        return bool(self.read_only_only and self.plugin_host and self.plugin_host.has_tool(name)
                    and self.plugin_host.tool_capabilities(name)['read_only'])

    async def execute(self,name,arguments): return str(await self.execute_result(name,arguments))

    async def execute_many(self,calls):
        async def run(name,args):
            try: return await self.execute(name,args)
            except Exception as error:return error
        return await asyncio.gather(*(run(*call) for call in calls))

    async def import_results(self,result_ids):
        for result_id in result_ids:
            result=await self.event_store.read_tool_observation(result_id,self.allowed_scopes)
            if result is None:raise ValueError('Transferred result does not belong to this scene')
            if result_id not in self.result_ids:self.result_ids.append(result_id)
            self.observations[result_id]=result
            call=await self.event_store.tool_observation_call(result_id,self.default_scene_id)
            if call and (call[0]=='read_web_media' or self.plugin_host and self.plugin_host.has_tool(call[0])):
                self.external_attempted=True
            if call and self.plugin_host and self.plugin_host.has_tool(call[0]) and self.is_read_only(call[0]) and result.status in {'ok','no_results','partial'}:
                self._cache[json.dumps(list(call),ensure_ascii=False,sort_keys=True)]=result

    def _resolve_arguments(self,name,args):
        if not self.references:return args
        refs=self.references
        for key,resolve in [('event_id',refs.locate_event),('message_ref',refs.locate_event),('actor_id',refs.actor_id),('asset_id',refs.media_id),
                            ('subject',refs.actor_id),('result_id',refs.result_id)]:
            if args.get(key):args[key]=resolve(args[key])
        if args.get('job_id'):args['job_id']=refs.job(args['job_id'])['id']
        return args

    async def execute_result(self,name,arguments):
        definitions={t['function']['name']:t for t in self.get_tool_definitions()}
        if name not in definitions:return ToolResult.failure('本入口未开放此工具','capability_denied')
        if name == 'read_message_range':
            try:
                arguments = MessageRangeArguments.model_validate(arguments).model_dump()
            except ValidationError as error:
                return ToolResult.failure(str(error), 'invalid_arguments')
        try:args=self._resolve_arguments(name,dict(arguments))
        except ValueError as error:return ToolResult.failure(str(error),'invalid_reference')
        if name=='read_tool_result':
            result=await self.event_store.read_tool_observation(args.get('result_id',''),self.allowed_scopes)
            if result is None:return ToolResult.failure('资料不存在或不属于本群','not_found')
            call=await self.event_store.tool_observation_call(result.result_id,self.default_scene_id)
            return (await self._present(call[0] if call else '',result,offset=int(args.get('offset',0)),limit=int(args.get('limit',6000))))
        if name=='tool_search':
            query=str(args.get('query','')).casefold().strip()
            if not query:return ToolResult.failure('query不能为空','invalid_arguments')
            matches=[t['function']['name'] for t in self.plugin_host.get_tool_definitions() if
                self.is_read_only(t['function']['name']) and t['function']['name'] not in self.unavailable_tools
                and query in (t['function']['name']+' '+t['function']['description']).casefold()] if self.plugin_host else []
            self.discovered_tools.update(matches[:8])
            return ToolResult(status='ok' if matches else 'no_results',content=json.dumps(matches[:8],ensure_ascii=False),coverage='tool_catalog')
        refresh=bool(args.pop('refresh',False));key=json.dumps([name,args],ensure_ascii=False,sort_keys=True)
        async with self._call_locks.setdefault(key,asyncio.Lock()):
            if not refresh and key in self._cache:return await self._present(name,self._cache[key].model_copy(update={'cached':True}))
            if self.checkpoint:await self.checkpoint('before_tool',{'scene_id':self.default_scene_id,'name':name,'arguments':args})
            start=time.monotonic()
            media_files=[]
            async with self._parallel:
                try:
                    if name=='read_web_media':
                        self.external_attempted=True
                        raw,media_files=await self.media_service.read_web_media(args.get('url',''),args.get('page'))
                    else:
                        if self.plugin_host and self.plugin_host.has_tool(name):self.external_attempted=True
                        raw=await self._execute_raw(name,args)
                    result=ToolResult.normalize(raw)
                    if isinstance(raw,list) and not raw:result.status='no_results'
                except Exception as error:result=ToolResult.failure(str(error),type(error).__name__)
            result.duration_ms=round((time.monotonic()-start)*1000,2)
            if not(self.plugin_host and self.plugin_host.has_tool(name)) and result.evidence_kind=='unknown':result.evidence_kind='retrieval'
            if name=='web_search' and result.status in {'error','unsupported'} and result.error_code!='invalid_arguments':
                self.unavailable_tools.add(name)
                result.content+='\n本次工作的搜索服务不可用，已停止继续调用；可读取已有官方链接，未核实部分写入unresolved，不用群史或旧知识代替发布事实。'
            result,event=await self.event_store.save_tool_observation(self.default_scene_id,name,args,result,
                background_work=self.read_only_only,media_files=media_files)
            self.result_ids.append(result.result_id)
            self.observations[result.result_id]=result
            if self.on_observation:await self.on_observation(event)
            if self.checkpoint:await self.checkpoint('after_tool',{'scene_id':self.default_scene_id,'name':name,'result':result.model_dump()})
            if self.plugin_host and self.plugin_host.has_tool(name) and result.status in {'ok','no_results','partial'}:self._cache[key]=result
        return await self._present(name,result)

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

    async def _present(self,name,result,offset=0,limit=6000):
        if not self.context:return result.page(offset,limit)
        if offset<0 or not 1<=limit<=12000:raise ValueError('offset must be nonnegative; limit must be 1..12000')
        refs=self.references;shown=result.model_copy(deep=True)
        if shown.result_id:shown.result_id=refs.register_result(shown.result_id)
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
            refs.register_event_range(event, start, end, total)
            shown.content = json.dumps({'message_ref':refs.register_event_locator(event.id),
                'actor':refs.register_actor(event.actor_id), 'range':[start,end,total],
                'text':item['text'], 'next_offset':end if end < total else None}, ensure_ascii=False)
            return shown
        local={'search_messages','read_context','query_timeline','query_person_history','search_media','query_memory','query_jobs'}
        if name not in local:return shown.page(offset,limit)
        try:data=json.loads(shown.content)
        except (TypeError,ValueError):return shown.page(offset,limit)
        if not isinstance(data,list):return shown.page(offset,limit)
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

    async def _execute_raw(self,name,args):
        store=self.event_store;scopes=self.allowed_scopes
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
            return [{'asset_id':x['id'],**{k:x[k] for k in ('scope','source_event_id','description','tags')}} for x in rows]
        if name=='query_jobs':
            if args.get('job_id'):
                job=await store.get_job(args['job_id'],self.default_scene_id);return [job] if job else []
            return await store.list_jobs(self.default_scene_id)
        if name=='calculate':return calculate(args.get('expression',''))
        if name=='finite_check':return await asyncio.to_thread(finite_check,**args)
        if self.read_only_only and self.plugin_host and self.plugin_host.has_tool(name) and self.is_read_only(name):
            return await self.plugin_host.execute_tool(name,args)
        rows=None;limit=max(1,min(int(args.get('limit',15)),50))
        if name=='search_messages':rows=await store.search_messages(str(args.get('query','')),scopes,limit,through_rowid=self.cutoff)
        elif name=='read_context':rows=await store.read_context(str(args.get('event_id','')),int(args.get('before',3)),int(args.get('after',3)),scopes,through_rowid=self.cutoff)
        elif name=='query_timeline':rows=await store.query_timeline(self.default_scene_id,float(args['start_time']),float(args['end_time']),scopes,limit,through_rowid=self.cutoff)
        elif name=='query_person_history':rows=await store.query_person_history(str(args.get('actor_id','')),scopes,limit,through_rowid=self.cutoff)
        elif name=='query_memory':
            if not self.memory_store:return ToolResult(status='unsupported',content='未配置认识账本')
            memories=await self.memory_store.query_memories(scopes,subject=args.get('subject'),kind=args.get('kind'),query=args.get('query'),include_superseded=bool(args.get('include_history',False)))
            return [m.model_dump(mode='json') for m in memories]
        if rows is not None:
            enriched=await store.project_reply_context(self.default_scene_id,[Event.model_validate(r) for r in rows],through_rowid=self.cutoff)
            if self.context:return [event.model_dump(mode='json') for event in enriched]
            return '\n'.join(project_event(event,self.bot_qq) for event in enriched)
        return ToolResult.failure('未知工具','not_found')
