"""Scoped local reads for conversation; external read-only capabilities for work."""
from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
import time
from dataclasses import dataclass, replace
from contextlib import nullcontext
from collections.abc import Callable

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model, model_validator

from len_bot.cognition.projection import project_event
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import DisplayedRange, ToolFieldError, ToolNextCall, ToolResult
from len_bot.tools.calculator import CALCULATE_TOOL, calculate
from len_bot.tools.finite_check import FINITE_CHECK_TOOL, finite_check
from len_bot.cognition.retrieval_models import RetrievalOptOut


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


class FindPersonArguments(ReadArguments):
    query: str = Field(min_length=1,pattern=r"\S",description='当前群已保存的QQ账号、昵称、群名片或明确称呼；U引用直接用于人物字段，不作为检索词')


class MemoryArguments(ReadArguments):
    subject: str | None = Field(default=None, min_length=1)
    kind: Literal['address', 'preference', 'relationship', 'fact', 'group_norm'] | None = None
    query: str | None = Field(default=None, min_length=1)
    include_history: bool = False
    start_time: float | None = Field(default=None,allow_inf_nan=False,description='认识建立时间起点，包含；不是原话事件时间')
    end_time: float | None = Field(default=None,allow_inf_nan=False,description='认识建立时间终点，不包含')

class SearchHistorySummariesArguments(ReadArguments):
    query: str = Field(min_length=1, pattern=r"\S")
    limit: int = Field(ge=1)
    start_time: float | None = Field(default=None, allow_inf_nan=False)
    end_time: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode='after')
    def ordered_range(self):
        if self.start_time is not None and self.end_time is not None and self.start_time >= self.end_time:
            raise ValueError('end_time must be later than start_time')
        return self


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
    'tool_search': ToolSearchArguments, 'find_person': FindPersonArguments,
    'search_history_summaries': SearchHistorySummariesArguments,
}


def read_tool(name, description):
    return {'type': 'function', 'function': {'name': name, 'description': description,
        'parameters': READ_ARGUMENT_MODELS[name].model_json_schema()}}


LOCAL_TOOLS = [
    read_tool('search_messages', '按文字查找本群已读截点之前的原话；只查询群消息，不检索外部网站或账号发布记录。'),
    read_tool('read_context', '读取消息M前后的本群原话。'),
    read_tool('query_timeline', '读取本群指定时间内的消息。'),
    read_tool('query_person_history', '读取本群人物U以前说过的话。'),
    read_tool('find_person', '按账号、昵称、群名片或已保存称呼定位本群人物；返回身份U和来源位置，同名分别列出，不读取全群原话。'),
    read_tool('query_memory', '按需读取本群认识及其来源；已撤销的认识不是当前事实。'),
    read_tool('search_history_summaries', '按需定位较早的已完成历史摘要；结果只是定位，精确原话仍需回读。'),
    read_tool('query_jobs', '对话中省略job_id读取本群工作的简短控制目录；指定已提供工作J读取详情字符页。目录不是完整结果，按detail_next_call或next_call继续已保存正文。'),
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
               for name in ('search_messages', 'query_timeline', 'query_person_history', 'search_history_summaries')},
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
        if self.call_context().role in {'conversation','work'}:
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
            if not capabilities['deferred'] or name in self.discovered_tools:
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
            if args.get(key):
                try:
                    args[key]=resolve(args[key])
                except ValueError:
                    return ToolResult.failure(f'{key}须使用本轮已提供的对应引用；先定位资料，不能自拟标识。',
                        'invalid_reference', stage='references', details=[ToolFieldError(loc=[key],
                            type='invalid_reference', message='引用未出现在本轮可定位资料中')])
        if args.get('job_id'):
            try:
                args['job_id']=refs.job(args['job_id'])['id']
            except ValueError:
                return ToolResult.failure('job_id须使用本轮已提供的工作引用；先用query_jobs读取工作目录。',
                    'invalid_reference', stage='references', details=[ToolFieldError(loc=['job_id'],
                        type='invalid_reference', message='工作引用未出现在本轮可定位资料中')])
        return args

    async def execute_result(self, name, arguments, *, tool_call_id=None) -> ToolResult:
        page = await self.execute_observation(name, arguments, tool_call_id=tool_call_id)
        result = await self._present(page.name, page.result, page.offset, page.limit, page.coordinate_unit)
        if result.status == 'error' and result.result_id is None:
            return (await self.error_observation(name, arguments, result, tool_call_id=tool_call_id)).result
        return result

    async def execute_observation(self, name, arguments, *, tool_call_id=None, read_slot_owned=False) -> ObservationPage:
        """Fetch and persist in parallel; references are granted only when presented."""
        definitions={t['function']['name']:t for t in self.get_tool_definitions()}
        registered_read=bool(self.plugin_host and self.plugin_host.has_registered_tool(name)
            and self.plugin_host.tool_capabilities(name)['kind']=='read')
        # AgentLoop already checked this model request's definition snapshot.
        # Catalog eviction only affects later requests; current plugin state
        # and scene/role access remain authoritative at execution time.
        if (registered_read and not self.plugin_host.has_tool(name,self.call_context())) or (
                not registered_read and name not in definitions):
            return await self.error_observation(name, arguments, ToolResult.failure(
                '本入口未开放此工具','capability_denied', stage='availability'), tool_call_id=tool_call_id)
        try:
            arguments = self._read_arguments(name, arguments)
        except ValidationError as error:
            return await self.error_observation(name, arguments, ToolResult.validation_failure(error), tool_call_id=tool_call_id)
        except ValueError as error:
            return await self.error_observation(name, arguments, ToolResult.failure(
                str(error), 'invalid_arguments', stage='arguments'), tool_call_id=tool_call_id)
        args=self._resolve_arguments(name,dict(arguments))
        if isinstance(args, ToolResult):
            return await self.error_observation(name, arguments, args, tool_call_id=tool_call_id)
        if name=='read_tool_result':
            offset, limit = args['offset'], args['limit']
            result=await self.event_store.read_tool_observation(args['result_id'],self.allowed_scopes)
            if result is None:
                return await self.error_observation(name, args, ToolResult.failure(
                    '资料不存在或不属于本群','not_found', stage='references'), tool_call_id=tool_call_id)
            self.observations[result.result_id]=result
            if result.result_id not in self.result_ids:self.result_ids.append(result.result_id)
            self._discover_continuation(result)
            call=await self.event_store.tool_observation_call(result.result_id,self.default_scene_id)
            if args['coordinate_unit']=='characters' and offset>len(result.content):
                return await self.error_observation(name, args, ToolResult.failure(
                    'offset超出已保存正文长度','invalid_arguments', stage='arguments',
                    details=[ToolFieldError(loc=['offset'],type='out_of_range',message='offset超出已保存正文长度')]),
                    tool_call_id=tool_call_id)
            if args['coordinate_unit']=='records':
                try:
                    records=json.loads(result.content)
                except ValueError:
                    records=None
                if call and call[0]=='read_pending_wakes' and isinstance(records,dict):
                    records=records['items']
                if not isinstance(records,list) or offset>len(records):
                    return await self.error_observation(name, args, ToolResult.failure(
                        '资料不是记录数组或offset超出记录数；按返回的坐标单位续读。','invalid_arguments', stage='arguments',
                        details=[ToolFieldError(loc=['coordinate_unit','offset'],type='invalid_record_range',
                            message='记录坐标与所读资料或范围不一致')]), tool_call_id=tool_call_id)
            return ObservationPage(call[0] if call and (args['coordinate_unit']=='records' or call[0]=='query_jobs') else 'read_tool_result',result,offset=offset,limit=limit,
                                   coordinate_unit=args['coordinate_unit'])
        if name=='tool_search':
            matches, categories = self.plugin_host.search_tools(args['query'], self.call_context(),
                kind='read') if self.plugin_host else ([], [])
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
            return await self.store_observation(name,args,result,tool_call_id=tool_call_id)
        if self.checkpoint:
            await self.checkpoint('before_tool', {'scene_id':self.default_scene_id,'name':name,'arguments':args})
        start = time.monotonic()
        media_files = []
        invocation = replace(self.call_context(), tool_call_id=tool_call_id, read_slot_owned=True)
        plugin_tool = bool(self.plugin_host and self.plugin_host.has_registered_tool(name))
        async with (nullcontext() if read_slot_owned else self._parallel):
            if plugin_tool:
                result = await self.plugin_host.execute_tool(name, args, invocation)
            else:
                try:
                    if name == 'read_web_media':
                        self.external_attempted = True
                        result, media_files = await self.media_service.read_web_media(args.get('url', ''), args.get('page'))
                    else:
                        result = await self._execute_raw(name, args)
                except ValidationError as error:
                    result = ToolResult.validation_failure(error, stage='execution', code='invalid_result')
                except Exception as error:
                    result = ToolResult.failure(str(error), type(error).__name__, stage='execution')
        result.duration_ms = round((time.monotonic()-start)*1000, 2)
        if result.evidence_kind == 'external':
            self.external_attempted = True
        if not plugin_tool and result.evidence_kind == 'unknown':
            result.evidence_kind = 'retrieval'
        return await self.store_observation(name,args,result,media_files=media_files,tool_call_id=tool_call_id)

    async def error_observation(self,name,args,result,*,tool_call_id=None):
        """Persist a returned error once, using the same observation path as reads."""
        page=result if isinstance(result,ObservationPage) else None
        result=page.result if page else result
        if result.result_id:
            return page or ObservationPage('read_tool_result', result, limit=self.page_chars)
        result=result.error_context(name,tool_call_id)
        return await self.store_observation(name,args,result,tool_call_id=tool_call_id)

    async def store_observation(self,name,args,result,*,media_files=(),tool_call_id=None):
        invocation = self.call_context()
        result = result.error_context(name,tool_call_id)
        if result.tool_name is None or result.tool_call_id is None:
            result = result.model_copy(update={'tool_name': result.tool_name or name,
                                              'tool_call_id': result.tool_call_id or tool_call_id})
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
        if self.plugin_host and self.plugin_host.has_registered_tool(name):
            requested=self.plugin_host.tool_capabilities(name)['page_chars']
            if requested is not None:page_chars=min(requested,self.max_chars)
        local_records = {'search_messages','read_context','query_timeline','query_person_history','find_person','search_media','query_memory','query_jobs','read_pending_wakes','search_history_summaries'}
        records = self.context and name in local_records and result.status != 'error'
        if name == 'query_jobs' and args.get('job_id'):
            records = False
        return ObservationPage('read_tool_result' if result.status == 'error' else name, result, limit=page_chars,
                               coordinate_unit='records' if records else 'characters')

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
            tool_name=page.result.tool_name,tool_call_id=page.result.tool_call_id,error_stage=page.result.error_stage,
            error_details=page.result.error_details,http_status=page.result.http_status,correction=page.result.correction,
            observation_event_id=page.result.observation_event_id, cached=page.result.cached)

    @staticmethod
    def _record_ranges(content):
        """Locate records in the saved JSON text, without reserializing offsets."""
        decoder = json.JSONDecoder()

        def skip_space(position):
            while position < len(content) and content[position] in ' \t\r\n':
                position += 1
            return position

        position = skip_space(0)
        if content[position] != '[':
            raise ValueError('Saved records must be a JSON array')
        ranges = []
        position = skip_space(position + 1)
        while content[position] != ']':
            start = position
            _, position = decoder.raw_decode(content, position)
            ranges.append((start, position))
            position = skip_space(position)
            if content[position] == ',':
                position = skip_space(position + 1)
        return ranges

    def _job_locator(self, job):
        refs = self.references
        return {'ref': refs.register_job(job), 'job_id': job['id'], 'revision': job['revision'],
                'status': job['status'], 'execution_status': job['execution_status'],
                'can_resume': job['can_resume'], 'work_operation': job['work_operation'],
                'requester': refs.register_actor('user:' + job['requester_qq_uid']) if job['requester_qq_uid'] else None,
                'request_source': refs.register_event_locator(job['request_source_event_id']) if job['request_source_event_id'] else None,
                'goal_preview': job['goal'][:160], 'details_not_provided': True}

    def _job_character_page(self, result, offset, limit):
        """A control locator accompanies only the exact saved JSON fragment."""
        shown = result.page(offset, limit)
        shown.result_id = self.references.register_result(result.result_id)
        if shown.next_call:
            shown.next_call.arguments['result_id'] = shown.result_id
        jobs = json.loads(result.content)
        positions = self._record_ranges(result.content)
        end = shown.displayed_range.end
        locators = [self._job_locator(job)
                    for job, (start, stop) in zip(jobs, positions) if start < end and stop > offset]
        # IDs are locations, not original human speech or retrieved bodies.
        # Grant them only after their complete literal appeared across actual
        # prior pages and this candidate page, including a split at a page edge.
        ranges = sorted([*self.presented_ranges.get(result.result_id, {}).get('characters', []), (offset, end)])
        merged = []
        for start, stop in ranges:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
            else:
                merged.append((start, stop))
        for job in jobs:
            for identifiers, register in ((job['result_ids'], self.references.register_result),
                                          (job['source_event_ids'], self.references.register_event_locator)):
                for ident in identifiers:
                    literal = json.dumps(ident, ensure_ascii=False)
                    position = result.content.find(literal)
                    while position != -1:
                        if any(start <= position and position + len(literal) <= stop for start, stop in merged):
                            register(ident)
                            break
                        position = result.content.find(literal, position + len(literal))
        shown.content = ('工作定位（不是完整详情或原话证据）：' + json.dumps(locators, ensure_ascii=False)
                         + '\n以下仅为已保存JSON正文的characters片段；displayed_range不包含上方定位：\n' + shown.content)
        shown.coverage = 'job_detail_page; displayed_range covers only the saved JSON fragment'
        return self._with_source_continuation(result, shown)

    async def _present(self, name, result, offset, limit, coordinate_unit='characters'):
        if offset < 0 or not 1 <= limit <= self.max_chars:
            raise ValueError(f'offset must be nonnegative; limit must be 1..{self.max_chars}')
        if (self.context and name == 'query_jobs' and coordinate_unit == 'characters'
                and result.status in {'ok', 'partial', 'no_results'}):
            return self._job_character_page(result, offset, limit)
        if not self.context and coordinate_unit == 'records':
            try:data=json.loads(result.content)
            except ValueError:data=None
            if not isinstance(data,list) or offset>len(data):
                return ToolResult.failure('资料不是记录数组或offset超出记录数；请按返回的坐标单位续读','invalid_arguments')
            end=offset
            while end<len(data) and len(json.dumps(data[offset:end+1],ensure_ascii=False))<=limit:end+=1
            if end==offset and end<len(data):
                return ToolResult.failure('limit不足以完整展示一条记录','page_too_small',stage='presentation')
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
            if end==offset and end<len(page['items']):
                return ToolResult.failure('limit不足以显示一条来源位置','page_too_small',stage='presentation')
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
        local={'search_messages','read_context','query_timeline','query_person_history','find_person','search_media','query_memory','query_jobs','search_history_summaries'}
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
        record_positions = self._record_ranges(result.content) if name == 'query_jobs' else None

        def project(raw, index):
            item=copy.deepcopy(raw);refs=self.references
            if history:
                event=Event.model_validate(item)
                if event.metadata['_rowid']>refs.cutoff:raise ValueError('Retrieved message exceeds read cutoff')
                return self.context.event_message(event)['content']
            if name=='search_media':
                return {'asset_id':refs.register_media(item.pop('asset_id')),**item}
            if name=='find_person':
                item['person']=refs.register_actor(item.pop('actor_id'))
                item['source_message']=refs.register_event_locator(item.pop('source_event_id'))
                for address in item['addresses']:
                    address['evidence']=[refs.register_event_locator(ident) for ident in address['evidence']]
                return item
            if name=='query_memory':
                editable=item['status']=='active' and (item['expires_at'] is None or item['expires_at']>self.context.runtime.clock())
                ref=refs.register_memory(item.pop('id'),editable=editable)
                item['subject']=refs.register_actor(item['subject'])
                item['evidence']=[refs.register_event_locator(e) for e in item['evidence']]
                item['revision_evidence']=[refs.register_event_locator(e) for e in item['revision_evidence']]
                return {'ref':ref,**item}
            if name == 'search_history_summaries':
                item['batch_id'] = item.pop('id')
                item['result_ref'] = shown.result_id
                item['key_events'] = [refs.register_event_locator(event_id) for event_id in item.pop('key_event_ids', [])]
                item['locator_only'] = True
                return item
            return {**self._job_locator(item), 'detail_next_call': {'name': 'read_tool_result', 'arguments': {
                'result_id': shown.result_id, 'offset': record_positions[index][0], 'limit': limit,
                'coordinate_unit': 'characters'}}}

        selected=[]
        index=offset
        while index<len(data):
            original=self.context.refs
            self.context.refs=copy.deepcopy(original)
            try:candidate=project(data[index], index)
            finally:self.context.refs=original
            candidate_text='\n'.join([*selected,candidate]) if history else json.dumps([*selected,candidate],ensure_ascii=False)
            if len(candidate_text)>limit:
                if not selected:
                    if not history:
                        if name == 'query_jobs':
                            return self._job_character_page(result, record_positions[index][0], limit)
                        return ToolResult.failure(f'limit太小，无法完整展示此条记录和引用；至少需要{len(candidate_text)}字符。',
                                                  'page_too_small',stage='presentation')
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
            selected.append(project(data[index], index))
            index+=1
        shown.content='\n'.join(selected) if history else json.dumps(selected,ensure_ascii=False)
        shown.truncated=index<len(data)
        shown.next_offset=index if shown.truncated else None
        shown.coordinate_unit='records'
        shown.displayed_range=DisplayedRange(start=offset,end=index,total=len(data)) if name != 'query_jobs' else None
        shown.next_call=ToolNextCall(name='read_tool_result',arguments={
            'result_id':shown.result_id,'offset':index,'limit':limit,'coordinate_unit':'records'}) if index<len(data) else None
        shown.coverage='record_page; offset is the next record index' if shown.truncated or offset else shown.coverage
        if name == 'query_jobs':
            shown.coverage = 'job_directory_locator; detail_next_call reads saved JSON, directory is not a full record read'
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

    def material_message(self, result_id):
        return self.material_view(self.observations[result_id])

    @staticmethod
    def material_view(original):
        shown=original.model_copy(update={'coordinate_unit':'characters',
            'displayed_range':DisplayedRange(start=0,end=len(original.content),total=len(original.content))})
        return {'role':'user','_context_section':'plugin_material','content':json.dumps({
            'kind':'plugin_material','observation':shown.model_dump(mode='json',exclude_none=True)},ensure_ascii=False)}

    def read_presentations(self,messages):
        """Describe adopted original bodies, never a trial page or locator."""
        presentations=[]
        seen=set()
        for message in messages:
            material=message.get('_context_section') in {'plugin_material','plugin_hook_material'}
            if message.get('role')!='tool' and not material:continue
            if not isinstance(message.get('content'),str):continue
            try:shown=json.loads(message['content'])
            except ValueError:continue
            if material:shown=shown.get('observation')
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
        if name=='find_person':
            query=args['query'].strip().casefold()
            if re.fullmatch(r'[murijpltbs]\d+',query):
                return ToolResult.failure('短引用是已知身份定位；直接用于对应字段，不作为人物姓名检索。','invalid_arguments',stage='arguments')
            people=await store.scene_member_locators(self.default_scene_id,self.cutoff)
            addresses=await self.memory_store.interaction_preferences(self.default_scene_id,
                [person['actor_id'] for person in people],now=self.call_context().now) if self.memory_store else []
            matches=[]
            for person in people:
                if person['actor_id']=='user:'+str(self.bot_qq):continue
                person['addresses']=[{'statement':item.statement,'evidence':item.evidence} for item in addresses
                    if item.subject==person['actor_id'] and item.kind=='address' and item.basis=='reported']
                names=[person['actor_id'].removeprefix('user:'),person['nickname'],person['card'],
                       *(item['statement'] for item in person['addresses'])]
                if any(query in value.casefold() for value in names if value):matches.append(person)
            total=len(matches)
            matches=matches[:self.config.retrieval_max_limit]
            return ToolResult(status='ok' if matches else 'no_results',content=json.dumps(matches,ensure_ascii=False),
                coverage=f'saved_scene_member_locators; matched={total}; returned={len(matches)}; original evidence unread',
                truncated=total>len(matches),evidence_kind='retrieval')
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
            usage = await self.media_service.recent_usage(self.default_scene_id, [item['id'] for item in rows],
                through_rowid=self.cutoff)
            records = [{'asset_id':x['id'],**{k:x[k] for k in ('scope','source_event_id','description','tags')},
                        **usage[x['id']]} for x in rows]
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
                job.update(runtime.plugin_host.work_details(job))
                job.pop('legacy_payload',None)
            return ToolResult(status='ok' if jobs else 'no_results', content=json.dumps(jobs, ensure_ascii=False),
                              coverage='current_jobs', evidence_kind='retrieval')
        if name=='calculate':return calculate(args.get('expression',''))
        if name=='finite_check':return await asyncio.to_thread(finite_check,**args)
        rows=None
        if name=='search_messages':rows=await store.search_messages(args['query'],scopes,args['limit'],through_rowid=self.cutoff)
        elif name=='read_context':rows=await store.read_context(args['event_id'],args['before'],args['after'],scopes,through_rowid=self.cutoff)
        elif name=='query_timeline':rows=await store.query_timeline(self.default_scene_id,args['start_time'],args['end_time'],scopes,args['limit'],through_rowid=self.cutoff)
        elif name=='query_person_history':rows=await store.query_person_history(args['actor_id'],scopes,args['limit'],through_rowid=self.cutoff)
        elif name=='search_history_summaries':
            summary_sql = "SELECT id,scene_id,start_rowid,start_offset,end_rowid,end_offset,summary,key_event_ids_json,generation_version FROM history_batches WHERE scene_id=? AND status='completed' AND end_rowid<=? AND instr(lower(summary),lower(?))>0"
            summary_params: list[Any] = [self.default_scene_id, self.cutoff, args['query']]
            if args.get('start_time') is not None:
                summary_sql += " AND EXISTS (SELECT 1 FROM events e WHERE e.scene_id=history_batches.scene_id AND e.id IN (SELECT value FROM json_each(history_batches.source_event_ids_json)) AND e.timestamp>=?)"
                summary_params.append(args['start_time'])
            if args.get('end_time') is not None:
                summary_sql += " AND EXISTS (SELECT 1 FROM events e WHERE e.scene_id=history_batches.scene_id AND e.id IN (SELECT value FROM json_each(history_batches.source_event_ids_json)) AND e.timestamp<?)"
                summary_params.append(args['end_time'])
            summary_sql += " ORDER BY end_rowid DESC LIMIT ?"; summary_params.append(args['limit'])
            cursor = await self.event_store._db.execute(summary_sql, summary_params)
            rows = [dict(zip([item[0] for item in cursor.description], row)) for row in await cursor.fetchall()]
            retrieval = self.context.runtime.retrieval_models if self.context else None
            profiles = getattr(self.context.runtime, 'retrieval_profiles', None) if self.context else None
            semantic_summary = False
            summary_partial = False
            if retrieval and profiles and profiles.embedding and self.context.runtime.memory_index and self.context.runtime.semantic_retrieval_enabled(self.default_scene_id):
                semantic_guard = self.context.runtime.semantic_index_guard(self.default_scene_id)
                if not semantic_guard():
                    raise RetrievalOptOut('该场景已关闭语义检索')
                summary_partial = (await self.context.runtime.memory_index.summary_coverage(self.default_scene_id)).get('pending', 0) > 0
                vector = (await retrieval.embed(profiles.embedding, [args['query']], scene_id=self.default_scene_id,
                                                 request_guard=semantic_guard))[0]
                if not semantic_guard():
                    raise RetrievalOptOut('该场景已关闭语义检索')
                ids = await self.context.runtime.memory_index.candidates(
                    self.default_scene_id, vector, limit=self.config.retrieval_max_limit,
                    source_kind='history_summary', max_end_rowid=self.cutoff,
                    start_time=args.get('start_time'), end_time=args.get('end_time'))
                if ids:
                    semantic_sql = "SELECT id,scene_id,start_rowid,start_offset,end_rowid,end_offset,summary,key_event_ids_json,generation_version FROM history_batches WHERE scene_id=? AND status='completed' AND end_rowid<=? AND id IN (SELECT value FROM json_each(?))"
                    semantic_params: list[Any] = [self.default_scene_id, self.cutoff, json.dumps(ids)]
                    if args.get('start_time') is not None:
                        semantic_sql += " AND EXISTS (SELECT 1 FROM events e WHERE e.scene_id=history_batches.scene_id AND e.id IN (SELECT value FROM json_each(history_batches.source_event_ids_json)) AND e.timestamp>=?)"; semantic_params.append(args['start_time'])
                    if args.get('end_time') is not None:
                        semantic_sql += " AND EXISTS (SELECT 1 FROM events e WHERE e.scene_id=history_batches.scene_id AND e.id IN (SELECT value FROM json_each(history_batches.source_event_ids_json)) AND e.timestamp<?)"; semantic_params.append(args['end_time'])
                    cursor = await self.event_store._db.execute(semantic_sql, semantic_params)
                    semantic_rows = [dict(zip([item[0] for item in cursor.description], row)) for row in await cursor.fetchall()]
                    by_id = {item['id']: item for item in rows}
                    by_id.update({item['id']: item for item in semantic_rows})
                    ordered_ids = list(dict.fromkeys([item['id'] for item in rows] + ids))
                    rows = [by_id[item] for item in ordered_ids if item in by_id][:args['limit']]
                    semantic_summary = True
                if not semantic_guard():
                    raise RetrievalOptOut('该场景已关闭语义检索')
            for row in rows:
                row['key_event_ids'] = json.loads(row.pop('key_event_ids_json'))
            return ToolResult(status='partial' if summary_partial else ('ok' if rows else 'no_results'), content=json.dumps(rows, ensure_ascii=False),
                              coverage='history_summary_locator+semantic' if semantic_summary else 'history_summary_locator', evidence_kind='retrieval')
        elif name=='query_memory':
            if not self.memory_store:return ToolResult(status='unsupported',content='未配置认识账本')
            aliases = {(self.default_scene_id,actor_id):tuple(value for value in (person.nickname,person.card) if value)
                       for actor_id,person in self.context.session.participants.items()} if self.context else None
            retrieval = self.context.runtime.retrieval_models if self.context else None
            profiles = getattr(self.context.runtime, 'retrieval_profiles', None) if self.context else None
            candidate_limit = self.config.retrieval_default_limit
            if args.get('query') and retrieval and profiles and profiles.embedding:
                candidate_limit = min(self.config.retrieval_max_limit, max(self.config.retrieval_default_limit, 24))
            memories=await self.memory_store.query_memories(scopes,subject=args.get('subject'),kind=args.get('kind'),query=args.get('query'),
                include_superseded=args['include_history'], limit=candidate_limit,
                start_time=args['start_time'],end_time=args['end_time'],subject_aliases=aliases)
            semantic_used = False
            semantic_partial = False
            if args.get('query') and retrieval and profiles and profiles.embedding and self.context.runtime.memory_index and self.context.runtime.semantic_retrieval_enabled(self.default_scene_id):
                semantic_guard = self.context.runtime.semantic_index_guard(self.default_scene_id)
                if not semantic_guard():
                    raise RetrievalOptOut('该场景已关闭语义检索')
                semantic_partial = (await self.context.runtime.memory_index.coverage(self.default_scene_id)).get('pending', 0) > 0
                vectors = await retrieval.embed(profiles.embedding, [args['query']], scene_id=self.default_scene_id,
                                                request_guard=semantic_guard)
                if not semantic_guard():
                    raise RetrievalOptOut('该场景已关闭语义检索')
                ids = await self.context.runtime.memory_index.candidates(
                    self.default_scene_id, vectors[0], limit=24,
                    subject=args.get('subject'), kind=args.get('kind'),
                    include_superseded=args['include_history'], start_time=args.get('start_time'),
                    end_time=args.get('end_time'))
                semantic = await self.memory_store.get_memories_by_ids(
                    ids, scopes, include_superseded=args['include_history'],
                    subject=args.get('subject'), kind=args.get('kind'),
                    start_time=args.get('start_time'), end_time=args.get('end_time'))
                by_id = {item.id:item for item in memories}
                lexical_ids = [item.id for item in memories]
                for item in semantic:
                    by_id.setdefault(item.id, item)
                lexical_rank = {item_id: index + 1 for index, item_id in enumerate(lexical_ids)}
                semantic_rank = {item_id: index + 1 for index, item_id in enumerate(ids) if item_id in by_id}
                memories = sorted(by_id.values(), key=lambda item: (
                    -(1 / (60 + lexical_rank.get(item.id, 10_000)) + 1 / (60 + semantic_rank.get(item.id, 10_000))), item.id))[:self.config.retrieval_default_limit]
                semantic_used = True
                if profiles.rerank and len(by_id) > self.config.retrieval_default_limit:
                    if not semantic_guard():
                        raise RetrievalOptOut('该场景已关闭语义检索')
                    ranked_ids = await retrieval.rerank(
                        profiles.rerank, args['query'], [item.statement for item in by_id.values()],
                        scene_id=self.default_scene_id, request_guard=semantic_guard)
                    ordered = list(by_id.values())
                    memories = [ordered[index] for index in ranked_ids[:self.config.retrieval_default_limit]]
                if not semantic_guard():
                    raise RetrievalOptOut('该场景已关闭语义检索')
                await self.event_store.save_trace(kind='memory_retrieval', scene_id=self.default_scene_id,
                    ref_id='memory-query:'+uuid.uuid4().hex,
                    payload={'query': args['query'], 'subject': args.get('subject'), 'kind': args.get('kind'),
                             'start_time': args.get('start_time'), 'end_time': args.get('end_time'),
                             'lexical_candidates': len(lexical_ids), 'semantic_candidates': len(semantic),
                             'rerank': bool(profiles.rerank and len(by_id) > self.config.retrieval_default_limit),
                             'index_partial': semantic_partial,
                             'candidate_memory_ids': [item.id for item in memories]})
            return ToolResult(status='partial' if semantic_partial else ('ok' if memories else 'no_results'),
                              content=json.dumps([m.model_dump(mode='json') for m in memories], ensure_ascii=False),
                              coverage='memory_ledger+semantic' if semantic_used else 'memory_ledger', evidence_kind='retrieval')
        if rows is not None:
            enriched=await store.project_reply_context(self.default_scene_id,[Event.model_validate(r) for r in rows],through_rowid=self.cutoff)
            if self.context:
                content = json.dumps([event.model_dump(mode='json') for event in enriched], ensure_ascii=False)
            else:
                content = '\n'.join(project_event(event, self.bot_qq) for event in enriched)
            return ToolResult(status='ok' if enriched else 'no_results', content=content,
                              coverage='original_messages', evidence_kind='retrieval')
        return ToolResult.failure('未知工具','not_found')
