"""Scoped local reads for conversation; external read-only capabilities for work."""
from __future__ import annotations

import asyncio
import copy
import json
import time
from dataclasses import dataclass
from collections.abc import Callable

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from len_bot.cognition.projection import project_event
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import DisplayedRange, ToolNextCall, ToolResult
from len_bot.tools.calculator import CALCULATE_TOOL, calculate
from len_bot.tools.finite_check import FINITE_CHECK_TOOL, finite_check


class ReadArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class SearchMessagesArguments(ReadArguments):
    query: str = Field(min_length=1, pattern=r"\S")
    limit: int = Field(ge=1)


class ReadContextArguments(ReadArguments):
    event_id: str = Field(min_length=1)
    before: int = Field(ge=0)
    after: int = Field(ge=0)


class TimelineArguments(ReadArguments):
    start_time: float = Field(allow_inf_nan=False)
    end_time: float = Field(allow_inf_nan=False)
    limit: int = Field(ge=1)

    @model_validator(mode='after')
    def ordered_range(self):
        if self.start_time > self.end_time:
            raise ValueError('start_time must not be later than end_time')
        return self


class PersonHistoryArguments(ReadArguments):
    actor_id: str = Field(min_length=1)
    limit: int = Field(ge=1)


class MemoryArguments(ReadArguments):
    subject: str | None = Field(default=None, min_length=1)
    kind: Literal['address', 'preference', 'relationship', 'fact', 'group_norm'] | None = None
    query: str | None = Field(default=None, min_length=1)
    include_history: bool = False
    start_time: float | None = Field(default=None,allow_inf_nan=False,description='认识建立时间起点，包含；不是原话事件时间')
    end_time: float | None = Field(default=None,allow_inf_nan=False,description='认识建立时间终点，不包含')


class JobsArguments(ReadArguments):
    job_id: str | None = Field(default=None, min_length=1)


class SearchMediaArguments(ReadArguments):
    query: str = ''
    curated_only: bool = True


class ReadMediaArguments(ReadArguments):
    asset_id: str = Field(min_length=1)


class WebMediaArguments(ReadArguments):
    url: str = Field(pattern=r"^https?://[^\s/?#]+(?:[/?#][^\s]*)?$")
    page: int | None = Field(default=None, ge=1, le=100, description='PDF页码从1开始，省略或null默认第一页；图片不传页码')


class ToolSearchArguments(ReadArguments):
    query: str = Field(min_length=1, pattern=r"\S", description='工具名称、中文别名或简短用途；保留原工具名与明确标识')


class MessageRangeArguments(ReadArguments):
    message_ref: str = Field(min_length=1)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)


class PendingWakeArguments(ReadArguments):
    after_rowid: int = Field(default=0, ge=0)
    limit: int = Field(ge=1)


class ToolResultArguments(ReadArguments):
    result_id: str = Field(min_length=1)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(ge=1)
    coordinate_unit: Literal['characters','records'] = Field(default='characters',description='原样复制next_call；characters读已存正文，records读本地目录记录')


READ_ARGUMENT_MODELS = {
    'search_messages': SearchMessagesArguments, 'read_context': ReadContextArguments,
    'query_timeline': TimelineArguments, 'query_person_history': PersonHistoryArguments,
    'query_memory': MemoryArguments, 'query_jobs': JobsArguments,
    'read_tool_result': ToolResultArguments, 'search_media': SearchMediaArguments,
    'read_media': ReadMediaArguments, 'read_message_range': MessageRangeArguments,
    'read_pending_wakes': PendingWakeArguments, 'read_web_media': WebMediaArguments,
    'tool_search': ToolSearchArguments,
}


def read_tool(name, description):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': READ_ARGUMENT_MODELS[name].model_json_schema()}}


LOCAL_TOOLS = [
    read_tool('search_messages', '按文字查找本群已读截点之前的原话。'),
    read_tool('read_context', '读取消息M前后的本群原话。'),
    read_tool('query_timeline', '读取本群指定时间内的消息。'),
    read_tool('query_person_history', '读取本群人物U以前说过的话。'),
    read_tool('query_memory', '按需读取本群认识及其来源；已撤销的认识不是当前事实。'),
    read_tool('query_jobs', '读取本群工作的实际版本、资料和进展。'),
    read_tool('read_tool_result', '继续阅读已获得的资料R；offset使用上次next_offset。'),
    read_tool('search_media', '按名称和描述查询本群或运营发布的图片。'),
    read_tool('read_media', '装入图片I/P像素和来源；动图只覆盖首帧。'),
]
READ_PENDING_WAKES = read_tool('read_pending_wakes',
    '分页定位当前场景尚未处理的唤醒来源，同时标注原话是否完整已读；使用next_after_rowid继续。目录不是原文证据，未读原话用read_context或read_message_range读取。')
READ_MESSAGE_RANGE = read_tool('read_message_range',
    '按字符范围继续读取消息M的原话。使用上次next_offset；片段不代表整条已读，原文全部覆盖后才能作为提案证据。')
READ_WEB_MEDIA = read_tool('read_web_media',
    '查看公开网页中的原图或PDF的一页，直接向模型提供像素。使用已知图片/PDF链接；不读取HTML页面。PDF页码从1开始，省略默认第1页。')
TOOL_SEARCH = read_tool('tool_search',
    '按名称、中文别名或用途发现当前群和职责可用的读取工具；结果含用途与关键参数提示。下一次请求获得选中工具的完整Schema，目录满时移除较早展开项，可再次发现。')
CORE_READ_TOOLS = [*LOCAL_TOOLS, READ_PENDING_WAKES, READ_MESSAGE_RANGE, READ_WEB_MEDIA,
                   TOOL_SEARCH, CALCULATE_TOOL, FINITE_CHECK_TOOL]


@dataclass(frozen=True)
class ObservationPage:
    """Stored observation awaiting a budgeted, scene-scoped presentation."""
    name: str
    result: ToolResult
    limit: int
    offset: int = 0
    coordinate_unit: Literal['characters','records'] = 'characters'


class RetrievalToolkit:
    def __init__(self,event_store,allowed_scopes,default_scene_id,memory_store=None,plugin_host=None,
                 bot_qq='',on_observation=None,checkpoint=None,media_service=None,
                 context=None, *, config: RuntimeConfig,
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
        self.config = config
        self.page_chars = config.tool_result_page_chars
        self.max_chars = config.tool_result_max_chars
        if context and context.refs.scene_id != default_scene_id:
            raise ValueError('Conversation context belongs to another scene')
        self.discovered_tools: dict[str, None] = {}
        self._argument_models = self._build_argument_models()
        self.result_ids=[]
        self.observations={}
        self.external_attempted=False
        self.unavailable_tools=set()
        self._parallel = asyncio.Semaphore(config.tool_read_concurrency)
        self.presented_ranges: dict[str,dict[str,list[tuple[int,int]]]] = {}

    @property
    def references(self): return self.context.refs if self.context else None
    @property
    def cutoff(self): return self.references.cutoff if self.references else self.call_context().cutoff_rowid

    def _build_argument_models(self):
        models = dict(READ_ARGUMENT_MODELS)
        bounds = {
            **{name: (self.config.retrieval_default_limit, self.config.retrieval_max_limit)
               for name in ('search_messages', 'query_timeline', 'query_person_history')},
            **{name: (self.page_chars, self.max_chars)
               for name in ('read_tool_result', 'read_message_range')},
            'read_pending_wakes': (self.config.pending_wakes_default_limit, self.config.pending_wakes_max_limit),
        }
        for name, (default, maximum) in bounds.items():
            models[name] = create_model(f'{models[name].__name__}Configured', __base__=models[name],
                limit=(int, Field(default=default, ge=1, le=maximum)))
        models['read_context'] = create_model('ReadContextArgumentsConfigured', __base__=ReadContextArguments,
            **{name: (int, Field(default=self.config.read_context_default_neighbors,
                               ge=0, le=self.config.read_context_max_neighbors)) for name in ('before', 'after')})
        return models

    def _read_arguments(self, name, arguments):
        model = self._argument_models.get(name)
        return model.model_validate(arguments).model_dump() if model else arguments

    def get_tool_definitions(self):
        definitions=copy.deepcopy(LOCAL_TOOLS)
        if self.context and self.context.pending_wakes():
            definitions.append(copy.deepcopy(READ_PENDING_WAKES))
        if self.context and (self.references.partial_events
                             or set(self.references.events.values()) - self.references.read_events):
            definitions.append(copy.deepcopy(READ_MESSAGE_RANGE))
        for definition in definitions:
            function = definition['function']
            function['parameters'] = self._argument_models[function['name']].model_json_schema()
        if not self.media_service: definitions=[t for t in definitions if t['function']['name'] not in {'search_media','read_media'}]
        if self.media_service:
            definitions.append(copy.deepcopy(READ_WEB_MEDIA))
        if self.call_context().role == 'work':
            definitions.append(copy.deepcopy(CALCULATE_TOOL))
            definitions.append(copy.deepcopy(FINITE_CHECK_TOOL))
        plugin_tools = self.plugin_host.get_tool_definitions(self.call_context(), kind='read') if self.plugin_host else []
        available_names = {item['function']['name'] for item in plugin_tools}
        self.discovered_tools = {name: None for name in self.discovered_tools if name in available_names}
        if plugin_tools:
            definitions.append(copy.deepcopy(TOOL_SEARCH))
        for definition in plugin_tools:
            name = definition['function']['name']
            capabilities = self.plugin_host.tool_capabilities(name)
            if name not in self.unavailable_tools and (not capabilities['deferred'] or name in self.discovered_tools):
                definitions.append(copy.deepcopy(definition))
        return definitions

    def _discover_continuation(self,result):
        continuation=result.source_next_call
        if (continuation is None or self.plugin_host is None
                or not self.plugin_host.has_tool(continuation.name,self.call_context())):
            return
        if self.plugin_host.tool_capabilities(continuation.name)['deferred']:
            self.discovered_tools.pop(continuation.name,None)
            self.discovered_tools[continuation.name]=None
            while len(self.discovered_tools)>self.config.tool_discovery_limit:
                self.discovered_tools.pop(next(iter(self.discovered_tools)))

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
        return await self._present(page.name, page.result, page.offset, page.limit, page.coordinate_unit)

    async def execute_observation(self, name, arguments) -> ObservationPage:
        """Fetch and persist in parallel; references are granted only when presented."""
        definitions={t['function']['name']:t for t in self.get_tool_definitions()}
        registered_read=bool(self.plugin_host and self.plugin_host.has_registered_tool(name)
            and self.plugin_host.tool_capabilities(name)['kind']=='read')
        # AgentLoop already checked this model request's definition snapshot.
        # Catalog eviction only affects later requests; current plugin state
        # and scene/role access remain authoritative at execution time.
        if (registered_read and not self.plugin_host.has_tool(name,self.call_context())) or (
                not registered_read and name not in definitions):
            return ObservationPage(name, ToolResult.failure('本入口未开放此工具','capability_denied'), limit=self.page_chars)
        try:
            arguments = self._read_arguments(name, arguments)
        except ValueError as error:
            return ObservationPage(name, ToolResult.failure(str(error), 'invalid_arguments'), limit=self.page_chars)
        try:args=self._resolve_arguments(name,dict(arguments))
        except ValueError as error:return ObservationPage(name, ToolResult.failure(str(error),'invalid_reference'), limit=self.page_chars)
        if name=='read_tool_result':
            offset, limit = args['offset'], args['limit']
            result=await self.event_store.read_tool_observation(args['result_id'],self.allowed_scopes)
            if result is None:return ObservationPage(name, ToolResult.failure('资料不存在或不属于本群','not_found'), limit=self.page_chars)
            self.observations[result.result_id]=result
            if result.result_id not in self.result_ids:self.result_ids.append(result.result_id)
            self._discover_continuation(result)
            call=await self.event_store.tool_observation_call(result.result_id,self.default_scene_id)
            if args['coordinate_unit']=='characters' and offset>len(result.content):
                return ObservationPage(name,ToolResult.failure('offset超出已保存正文长度','invalid_arguments'),limit=self.page_chars)
            return ObservationPage(call[0] if call and args['coordinate_unit']=='records' else 'read_tool_result',result,offset=offset,limit=limit,
                                   coordinate_unit=args['coordinate_unit'])
        if name=='tool_search':
            matches, categories = self.plugin_host.search_tools(args['query'], self.call_context(),
                kind='read', excluded=self.unavailable_tools) if self.plugin_host else ([], [])
            selected = matches[:self.config.tool_discovery_limit]
            for item in selected:
                tool_name = item['name']
                if self.plugin_host.tool_capabilities(tool_name)['deferred']:
                    self.discovered_tools.pop(tool_name, None)
                    self.discovered_tools[tool_name] = None
            while len(self.discovered_tools) > self.config.tool_discovery_limit:
                self.discovered_tools.pop(next(iter(self.discovered_tools)))
            result = ToolResult(status='ok' if selected else 'no_results',
                content=json.dumps({'tools': selected, 'available_categories': categories if not selected else [],
                    'expanded_catalog_limit': self.config.tool_discovery_limit,
                    'note': '下次请求提供选中工具的完整Schema；较早展开项可再次搜索。' if selected else '未匹配当前允许的能力；可用所列类别换一种表达。'}, ensure_ascii=False),
                coverage='tool_catalog',evidence_kind='retrieval')
            return await self.store_observation(name,args,result)
        if self.checkpoint:
            await self.checkpoint('before_tool', {'scene_id':self.default_scene_id,'name':name,'arguments':args})
        start = time.monotonic()
        media_files = []
        invocation = self.call_context()
        plugin_tool = bool(self.plugin_host and self.plugin_host.has_registered_tool(name))
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
            if result.error_code == 'search_unavailable':
                self.unavailable_tools.add(name)
                result.content += '\n本次工作已确认搜索服务未提供可读取结果；可读取已有链接，未核实部分写入unresolved。'
        return await self.store_observation(name,args,result,media_files=media_files)

    async def store_observation(self,name,args,result,*,media_files=()):
        invocation = self.call_context()
        result, event = await self.event_store.save_tool_observation(self.default_scene_id, name, args, result,
            background_work=invocation.role == 'work', media_files=media_files)
        self.result_ids.append(result.result_id)
        self.observations[result.result_id] = result
        self._discover_continuation(result)
        if self.on_observation:
            await self.on_observation(event)
        if self.checkpoint:
            await self.checkpoint('after_tool', {'scene_id':self.default_scene_id,'name':name,'result':result.model_dump()})
        page_chars = self.page_chars
        if name == 'read_group_chat_window':
            plugin = self.plugin_host.get_plugin('group_summary')
            page_chars = min(plugin.config.page_chars,self.max_chars)
        local_records = {'search_messages','read_context','query_timeline','query_person_history','search_media','query_memory','query_jobs','read_pending_wakes'}
        return ObservationPage(name, result, limit=page_chars,
                               coordinate_unit='records' if self.context and name in local_records else 'characters')

    def validate_conclusion_sources(self, result_ids, unresolved):
        """Validate evidence availability, not the semantic truth of a conclusion."""
        if not set(result_ids).issubset(self.result_ids):
            raise ValueError('Result references resources this work has not observed')
        if unresolved:
            return
        sources=[self.observations[ident] for ident in result_ids]
        if any(item.status not in {'ok','partial','no_results'} for item in sources):
            raise ValueError('完成结论引用了失败或不可用结果。请补充有效依据，或把尚未核实的要求写入unresolved。')
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
            coordinate_unit=page.coordinate_unit,
            next_call=ToolNextCall(name='read_tool_result',arguments={
                'result_id':self.references.register_result(page.result.result_id) if self.references else page.result.result_id,
                'offset':page.offset,'limit':page.limit,'coordinate_unit':page.coordinate_unit}),
            source_truncated=page.result.truncated,
            coverage='result_locator; content not read', evidence_kind=page.result.evidence_kind,
            error_code=page.result.error_code, fetched_at=page.result.fetched_at,
            observation_event_id=page.result.observation_event_id, cached=page.result.cached)

    async def _present(self, name, result, offset, limit, coordinate_unit='characters'):
        if offset < 0 or not 1 <= limit <= self.max_chars:
            raise ValueError(f'offset must be nonnegative; limit must be 1..{self.max_chars}')
        if not self.context and coordinate_unit == 'records':
            try:data=json.loads(result.content)
            except ValueError:data=None
            if not isinstance(data,list) or offset>len(data):
                return ToolResult.failure('资料不是记录数组或offset超出记录数；请按返回的坐标单位续读','invalid_arguments')
            end=offset
            while end<len(data) and len(json.dumps(data[offset:end+1],ensure_ascii=False))<=limit:end+=1
            if end==offset and end<len(data):return ToolResult.failure('limit不足以完整展示一条记录','page_too_small')
            return result.model_copy(update={'content':json.dumps(data[offset:end],ensure_ascii=False),
                'coordinate_unit':'records','displayed_range':DisplayedRange(start=offset,end=end,total=len(data)),
                'next_offset':end if end<len(data) else None,'truncated':end<len(data),
                'next_call':ToolNextCall(name='read_tool_result',arguments={'result_id':result.result_id,
                    'offset':end,'limit':limit,'coordinate_unit':'records'}) if end<len(data) else None})
        if not self.context or coordinate_unit == 'characters' and name not in {'read_pending_wakes','read_message_range'}:
            shown = result.page(offset,limit)
            if self.context and shown.result_id:
                shown.result_id = self.references.register_result(shown.result_id)
                if shown.next_call:shown.next_call.arguments['result_id']=shown.result_id
            return self._with_source_continuation(result,shown)
        refs=self.references;shown=result.model_copy(deep=True)
        if shown.result_id:shown.result_id=refs.register_result(shown.result_id)
        if name == 'read_pending_wakes':
            if shown.status not in {'ok','partial'}:return shown
            page = json.loads(shown.content)
            ids = {item['event_id'] for item in page['items']}
            events = await self.event_store.events_by_ids(self.default_scene_id,ids,self.cutoff)
            if {event.id for event in events} != ids:
                return ToolResult.failure('目录原始来源不属于本场景或超出本轮截点','invalid_source')
            if offset>len(page['items']):return ToolResult.failure('offset超出当前已保存目录页','invalid_arguments')
            end=offset
            while end<len(page['items']):
                trial={**page,'items':page['items'][offset:end+1]}
                if len(json.dumps(self.context.project_pending_page(trial),ensure_ascii=False))>limit:break
                end+=1
            if end==offset and end<len(page['items']):return ToolResult.failure('limit不足以显示一条来源位置','page_too_small')
            shown.content = json.dumps(self.context.project_pending_page({**page,'items':page['items'][offset:end]}),ensure_ascii=False)
            shown.coordinate_unit='records'
            shown.displayed_range=DisplayedRange(start=offset,end=end,total=len(page['items']))
            shown.truncated=end<len(page['items'])
            shown.next_offset=end if shown.truncated else None
            shown.next_call=ToolNextCall(name='read_tool_result',arguments={'result_id':shown.result_id,
                'offset':end,'limit':limit,'coordinate_unit':'records'}) if shown.truncated else None
            if not shown.truncated and page['next_after_rowid'] is not None:
                shown.source_next_call=ToolNextCall(name='read_pending_wakes',arguments={
                    'after_rowid':page['next_after_rowid'],'limit':self.config.pending_wakes_default_limit})
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
            shown.coordinate_unit='characters'
            # These coordinates belong to the human message, not the JSON
            # wrapper saved under this result_id. Original read ranges live in
            # TurnReferences and in content.range.
            shown.displayed_range=None
            shown.next_call = ToolNextCall(name='read_message_range',arguments={
                'message_ref':refs.register_event_locator(event.id),'offset':end,'limit':limit}) if end < total else None
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
        if offset>len(data):return ToolResult.failure('offset超出当前已保存记录数','invalid_arguments')
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
        index=offset
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
                    shown.coordinate_unit = 'records'
                    shown.displayed_range = DisplayedRange(start=index,end=index,total=len(data))
                    shown.next_call = ToolNextCall(name='read_message_range',arguments={
                        'message_ref':refs.register_event_locator(event.id),'offset':0,'limit':limit})
                    return shown
                break
            selected.append(project(data[index]))
            index+=1
        shown.content='\n'.join(selected) if history else json.dumps(selected,ensure_ascii=False)
        shown.truncated=index<len(data)
        shown.next_offset=index if shown.truncated else None
        shown.coordinate_unit='records'
        shown.displayed_range=DisplayedRange(start=offset,end=index,total=len(data))
        shown.next_call=ToolNextCall(name='read_tool_result',arguments={
            'result_id':shown.result_id,'offset':index,'limit':limit,'coordinate_unit':'records'}) if index<len(data) else None
        shown.coverage='record_page; offset is the next record index' if shown.truncated or offset else shown.coverage
        return shown

    def _with_source_continuation(self, original, shown):
        if not original.source_next_call or not original.result_id or shown.displayed_range is None:
            return shown
        span=shown.displayed_range
        intervals=sorted([*self.presented_ranges.get(original.result_id,{}).get('characters',[]),(span.start,span.end)])
        covered=0
        for start,end in intervals:
            if start>covered:break
            covered=max(covered,end)
        if covered>=len(original.content):shown.source_next_call=original.source_next_call.model_copy(deep=True)
        return shown

    def read_presentations(self,messages):
        """Describe adopted original bodies, never a trial page or locator."""
        presentations=[]
        seen=set()
        for message in messages:
            if message.get('role')!='tool' or not isinstance(message.get('content'),str):continue
            try:shown=json.loads(message['content'])
            except ValueError:continue
            if not isinstance(shown,dict) or 'locator' in shown.get('coverage',''):continue
            ident=shown.get('result_id')
            if self.references and ident in self.references.results:ident=self.references.results[ident]
            original=self.observations.get(ident)
            span=shown.get('displayed_range')
            if original is None or not isinstance(span,dict):continue
            unit=shown.get('coordinate_unit','characters')
            if unit=='characters':total=len(original.content)
            elif unit=='records':
                try:records=json.loads(original.content)
                except ValueError:continue
                if not isinstance(records,list):continue
                total=len(records)
            else:continue
            if span.get('total')!=total or not 0<=span.get('start',-1)<=span.get('end',-1)<=total:continue
            key=(ident,unit,span['start'],span['end'])
            if key not in seen:
                presentations.append({'result_id':ident,'coordinate_unit':unit,**span})
                seen.add(key)
        return presentations

    def adopt_presentations(self,presentations):
        for item in presentations:
            units=self.presented_ranges.setdefault(item['result_id'],{})
            ranges=sorted([*units.get(item['coordinate_unit'],[]),(item['start'],item['end'])])
            merged=[]
            for start,end in ranges:
                if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(merged[-1][1],end))
                else:merged.append((start,end))
            units[item['coordinate_unit']]=merged

    def restore_presentations(self,reads):
        self.adopt_presentations([{'result_id':ident,'coordinate_unit':unit,'start':start,'end':end}
            for ident,units in reads.items() for unit,record in units.items() for start,end in record['ranges']])

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
            rows=await store.list_media([self.default_scene_id,'global-safe'],query=args['query'],
                curated_only=args['curated_only'], limit=self.config.media_search_limit)
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
                runtime=self.context.runtime if self.context else self.plugin_host.runtime
                if job['can_resume']:
                    job['resume_issue']=runtime.job_resume_issue(job)
                    job['can_resume']=job['resume_issue'] is None
                if job['summary_coverage'] is not None:
                    job['summary_coverage'] = {key:value for key,value in job['summary_coverage'].items()
                                               if key not in {'read_result_ranges', 'read_event_ids'}}
            return ToolResult(status='ok' if jobs else 'no_results', content=json.dumps(jobs, ensure_ascii=False),
                              coverage='current_jobs', evidence_kind='retrieval')
        if name=='calculate':return calculate(args.get('expression',''))
        if name=='finite_check':return await asyncio.to_thread(finite_check,**args)
        rows=None
        if name=='search_messages':rows=await store.search_messages(args['query'],scopes,args['limit'],through_rowid=self.cutoff)
        elif name=='read_context':rows=await store.read_context(args['event_id'],args['before'],args['after'],scopes,through_rowid=self.cutoff)
        elif name=='query_timeline':rows=await store.query_timeline(self.default_scene_id,args['start_time'],args['end_time'],scopes,args['limit'],through_rowid=self.cutoff)
        elif name=='query_person_history':rows=await store.query_person_history(args['actor_id'],scopes,args['limit'],through_rowid=self.cutoff)
        elif name=='query_memory':
            if not self.memory_store:return ToolResult(status='unsupported',content='未配置认识账本')
            aliases = {(self.default_scene_id,actor_id):tuple(value for value in (person.nickname,person.card) if value)
                       for actor_id,person in self.context.session.participants.items()} if self.context else None
            memories=await self.memory_store.query_memories(scopes,subject=args.get('subject'),kind=args.get('kind'),query=args.get('query'),
                include_superseded=args['include_history'], limit=self.config.retrieval_default_limit,
                start_time=args['start_time'],end_time=args['end_time'],subject_aliases=aliases)
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
