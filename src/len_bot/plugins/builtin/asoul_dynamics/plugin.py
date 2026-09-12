"""Specific site reads returned through LenBot's existing observation pipeline."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginCallContext
from len_bot.tools.results import ToolNextCall, ToolResult, ToolSource

from .client import DynamicsClient, DynamicsLookupError, SourceSnapshot
from .config import DynamicsConfig
from .models import CardRequest, DetailRequest, FanartCardRequest, FanartSearchRequest, LatestRequest, OnThisDayRequest, RandomFanartRequest, SearchRequest

if TYPE_CHECKING:
    from len_bot.config_store import MemberSettings, TimeSettings


class AsoulDynamicsPlugin(BasePlugin):
    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.context = context
        self.config: DynamicsConfig = context.config
        self.zone = ZoneInfo(context.time_settings.timezone)
        self.member_keywords = tuple(value for member in context.members for value in (member.name, *member.aliases))
        self.client = DynamicsClient(self.config, context.members)

    async def on_load(self, context: PluginContext):
        definitions = [
            ("get_asoul_dynamics", LatestRequest, self.get_latest, False,
             "读取该源已抓取的最新动态，按源publishedAt排序。关键词或历史内容用search_asoul_dynamics。member须为已配置成员/别名，null明确查全部；limit=null用已配置页量。图片仅给来源链接，未加载像素。"),
            ("search_asoul_dynamics", SearchRequest, self.search, False,
             "按关键词检索成员历史动态，用户要求搜历史动态或回找曾发过的内容时使用。群消息和网页索引不能代替这个源。query/member/cursor/dynamic_type为null时不加对应筛选，sort/limit为null时使用配置。先读完本次正文，再复制source_next_call取得下一批。未知成员失败，不改查全员。"),
            ("read_asoul_dynamic", DetailRequest, self.read_dynamic, True,
             "读取已取得且尚新鲜的源动态记录。没有详情接口；范围保持为源查询提供的内容，不能视作平台原动态全文或看过图片。"),
            ("render_asoul_dynamic_card", CardRequest, self.render_card, True,
             "把指定已保存查询资料 result_ref 中的 item_id 渲染为统一亮色卡片并登记图片；也兼容本进程已取得的 dynamic_id。不重新查询、不自动发送，也不表示已读取源图片像素。"),
            ("render_asoul_fanart_card", FanartCardRequest, self.render_fanart_card, True,
             "把指定已保存查询资料 result_ref 中的二创 item_id 渲染为统一亮色卡片并登记图片；仅使用来源字段，不把图片链接说成已读取像素。"),
            ("get_asoul_on_this_day", OnThisDayRequest, self.on_this_day, True,
             "查询源站历史同日内容；month_day为MM-DD，null使用已配置业务时区的今天；limit=null使用配置页量。不自动播报。"),
            ("search_asoul_fanart", FanartSearchRequest, self.search_fanart, True,
             "按源站二创标签检索。query/character/content_type/category/kind/cursor的null均为不筛选；character是源标签（可含团体），不自动展开成员。sort/limit为null时来自配置，读完当前正文再复制source_next_call。公开图片链接可用read_web_media取得像素和可发送的场景资产引用。"),
            ("get_random_asoul_fanart", RandomFanartRequest, self.random_fanart, True,
             "按明确源标签随机取一条二创；null为不筛选。每次执行是独立随机查询，仅返回观察，不主动发送。公开图片链接可用read_web_media取得像素和场景资产引用，再由respond提出图片发送；链接本身不代表已看像素。"),
        ]
        discovery = {
            "get_asoul_dynamics": ("读取成员最近动态", ("最新动态", "最近动态"), ("动态", "最近", "最新", "近况")),
            "search_asoul_dynamics": ("按关键词检索成员历史动态", ("历史动态", "搜索动态", "查找动态", "动态关键词"), ("动态", "历史", "检索", "搜索", "关键词", "以前发过")),
            "read_asoul_dynamic": ("回读已取得的动态记录", ("动态详情", "读动态"), ("动态", "详情", "记录", "原文")),
            "render_asoul_dynamic_card": ("渲染一条动态卡片", ("动态卡片", "做动态卡片"), ("动态", "卡片", "图片")),
            "render_asoul_fanart_card": ("渲染一条二创卡片", ("二创卡片", "做二创卡片"), ("二创", "同人", "卡片", "图片")),
            "get_asoul_on_this_day": ("查询历史同日动态", ("历史上的今天", "历史同日", "那年今日"), ("往年", "同日", "今天", "历史", "回顾")),
            "search_asoul_fanart": ("按标签查找二创作品", ("二创", "同人图", "查找二创"), ("二创", "同人", "图片", "作品", "标签", "找一张", "发来")),
            "get_random_asoul_fanart": ("随机读取一条二创作品", ("随机二创", "来张二创", "随机同人图"), ("随机", "二创", "同人", "图片", "抽一张", "来一张", "发来")),
        }
        for name, schema, handler, deferred, description in definitions:
            purpose, aliases, keywords = discovery[name]
            context.register_tool(name=name, description=description, parameter_model=schema,
                purpose=purpose, aliases=aliases, keywords=(*keywords, *self.member_keywords),
                handler=handler, kind="read", roles=("conversation", "work"), deferred=deferred)

    async def on_unload(self):
        await self.client.close()

    def _result(self, snapshot: SourceSnapshot, *, cached: bool, coverage: str, data: dict | None = None,
                source_next_call: ToolNextCall | None = None) -> ToolResult:
        payload = {
            "scope": "该源已抓取的内容；不代表全平台绝对最新",
            "coverage": coverage,
            "fetched_at": snapshot.fetched_at,
            "source_url": snapshot.url,
            "pixels_loaded": False,
            "content_scope": "保留源查询返回的全部字段；未取得平台原动态详情，片段不扩写为全文。",
            "media_read": "公开图片或PDF链接可交read_web_media；仅当观察返回已登记的attachments后，才有可回读、可供respond引用的场景资产。",
            "data": data if data is not None else snapshot.data,
        }
        items = payload["data"].get("items")
        status = "no_results" if items == [] else "ok"
        return ToolResult(status=status, content=json.dumps(payload, ensure_ascii=False),
            sources=[ToolSource(url=snapshot.url, title="A-SOUL 动态查询站")],
            fetched_at=snapshot.fetched_at, cached=cached, coverage=coverage, evidence_kind="external",
            source_next_call=source_next_call)

    async def get_latest(self, request: LatestRequest, call_context: PluginCallContext) -> ToolResult:
        limit = self.config.default_limit if request.limit is None else request.limit
        try:
            snapshot, cached = await self.client.search(query=None, member=request.member, cursor=None,
                dynamic_type=None, sort="newest", limit=limit)
        except DynamicsLookupError as error:
            return ToolResult.failure(str(error), error.code)
        data = copy.deepcopy(snapshot.data)
        data["items"].sort(key=lambda item: datetime.fromisoformat(item["publishedAt"]), reverse=True)
        continuation = ToolNextCall(name="search_asoul_dynamics", arguments={
            "query": None, "member": request.member, "cursor": data["nextCursor"],
            "dynamic_type": None, "sort": "newest", "limit": limit,
        }) if data["nextCursor"] else None
        return self._result(snapshot, cached=cached, coverage="source_latest_query_records", data=data,
            source_next_call=continuation)

    async def search(self, request: SearchRequest, call_context: PluginCallContext) -> ToolResult:
        sort = self.config.search_sort if request.sort is None else request.sort
        limit = self.config.default_limit if request.limit is None else request.limit
        try:
            snapshot, cached = await self.client.search(query=request.query, member=request.member,
                cursor=request.cursor, dynamic_type=request.dynamic_type, sort=sort, limit=limit)
        except DynamicsLookupError as error:
            return ToolResult.failure(str(error), error.code)
        continuation = ToolNextCall(name="search_asoul_dynamics", arguments={**request.model_dump(),
            "cursor": snapshot.data["nextCursor"], "sort": sort, "limit": limit}) if snapshot.data["nextCursor"] else None
        return self._result(snapshot, cached=cached, coverage="source_search_records", source_next_call=continuation)

    async def read_dynamic(self, request: DetailRequest, call_context: PluginCallContext) -> ToolResult:
        try:
            snapshot = self.client.read_obtained_dynamic(request.dynamic_id)
        except DynamicsLookupError as error:
            return ToolResult.failure(str(error), error.code)
        return self._result(snapshot, cached=True, coverage="retrieved_source_record")

    async def render_card(self, request: CardRequest, call_context: PluginCallContext) -> ToolResult:
        try:
            snapshot = await self._card_snapshot(request.result_ref, request.item_id, request.dynamic_id, call_context)
            from .render import render_dynamic_card
            from pathlib import Path
            import asyncio
            font = Path(self.context.directory).parent / "asoul_calendar" / "resources" / "font.ttf"
            if not font.is_file():
                font = Path(self.context.directory) / "resources" / "font.ttf"
            png = await asyncio.to_thread(render_dynamic_card, snapshot.data, font)
            asset_id = await call_context.save_image(png, "A-SOUL 动态亮色卡片")
        except DynamicsLookupError as error:
            return ToolResult.failure(str(error), error.code)
        except (OSError, ValueError) as error:
            return ToolResult.failure(str(error), "card_render_failed", stage="presentation")
        return ToolResult(status="ok", coverage="rendered_source_dynamic_card", evidence_kind="external",
            attachments=[asset_id], content=json.dumps({"dynamic_id": request.item_id or request.dynamic_id,
                "asset_id": asset_id, "source_url": snapshot.data.get("url"),
                "theme_version": "light-v1", "pixels_loaded": False,
                "note": "卡片由已取得的源字段确定性渲染；未读取源图片像素。"}, ensure_ascii=False),
            sources=[ToolSource(url=snapshot.data.get("url") or snapshot.data.get("sourceDynamicUrl", ""), title="A-SOUL 动态查询站")],
            fetched_at=snapshot.fetched_at, cached=True)

    async def render_fanart_card(self, request: FanartCardRequest, call_context: PluginCallContext) -> ToolResult:
        try:
            snapshot = await self._card_snapshot(request.result_ref, request.item_id, request.source_dynamic_id, call_context)
            from .render import render_dynamic_card
            from pathlib import Path
            import asyncio
            font = Path(self.context.directory).parent / "asoul_calendar" / "resources" / "font.ttf"
            if not font.is_file():
                font = Path(self.context.directory) / "resources" / "font.ttf"
            png = await asyncio.to_thread(render_dynamic_card, snapshot.data, font, kind="二创来源")
            asset_id = await call_context.save_image(png, "A-SOUL 二创亮色卡片")
        except DynamicsLookupError as error:
            return ToolResult.failure(str(error), error.code)
        except (OSError, ValueError) as error:
            return ToolResult.failure(str(error), "card_render_failed", stage="presentation")
        return ToolResult(status="ok", coverage="rendered_source_fanart_card", evidence_kind="external",
            attachments=[asset_id], content=json.dumps({"source_dynamic_id": request.item_id or request.source_dynamic_id,
                "asset_id": asset_id, "theme_version": "light-v1", "pixels_loaded": False}, ensure_ascii=False),
            sources=[ToolSource(url=snapshot.data.get("url") or snapshot.data.get("sourceDynamicUrl", ""), title="A-SOUL 二创来源")],
            fetched_at=snapshot.fetched_at, cached=True)

    async def _card_snapshot(self, result_ref, item_id, obtained_id, call_context):
        if result_ref:
            observation = await call_context.plugin.event_store.read_tool_observation(result_ref, [call_context.scene_id])
            if observation is None:
                raise DynamicsLookupError('卡片资料不属于当前场景或已不存在。', 'invalid_result_ref')
            try:
                payload = json.loads(observation.content)
            except json.JSONDecodeError as error:
                raise DynamicsLookupError('卡片资料不是有效的动态查询结果。', 'invalid_result_ref') from error
            data = payload.get('data', payload)
            items = data.get('items') if isinstance(data, dict) else None
            if not isinstance(items, list):
                items = [data] if isinstance(data, dict) else []
            record = next((item for item in items if isinstance(item, dict) and
                           (item.get('dynamicId') == item_id or item.get('sourceDynamicId') == item_id)), None)
            if record is None:
                raise DynamicsLookupError('卡片 item_id 不在指定的已保存查询资料中。', 'item_not_in_result')
            url = observation.sources[0].url if observation.sources else ''
            return SourceSnapshot(record, observation.fetched_at, observation.fetched_at, url)
        return self.client.read_obtained_dynamic(obtained_id)

    async def on_this_day(self, request: OnThisDayRequest, call_context: PluginCallContext) -> ToolResult:
        month_day = request.month_day
        if month_day is None:
            month_day = datetime.fromtimestamp(call_context.now, self.zone).strftime("%m-%d")
        limit = self.config.on_this_day_default_limit if request.limit is None else request.limit
        snapshot, cached = await self.client.on_this_day(month_day, limit)
        return self._result(snapshot, cached=cached, coverage="source_historical_day_records")

    async def search_fanart(self, request: FanartSearchRequest, call_context: PluginCallContext) -> ToolResult:
        sort = self.config.fanart_sort if request.sort is None else request.sort
        limit = self.config.default_limit if request.limit is None else request.limit
        snapshot, cached = await self.client.fanart(query=request.query, character=request.character,
            content_type=request.content_type, category=request.category, kind=request.kind,
            cursor=request.cursor, random=False, sort=sort, limit=limit)
        continuation = ToolNextCall(name="search_asoul_fanart", arguments={**request.model_dump(),
            "cursor": snapshot.data["nextCursor"], "sort": sort, "limit": limit}) if snapshot.data["nextCursor"] else None
        return self._result(snapshot, cached=cached, coverage="source_fanart_search_records", source_next_call=continuation)

    async def random_fanart(self, request: RandomFanartRequest, call_context: PluginCallContext) -> ToolResult:
        snapshot, cached = await self.client.fanart(query=None, character=request.character,
            content_type=request.content_type, category=request.category, kind=request.kind,
            cursor=None, random=True, sort=None, limit=1)
        return self._result(snapshot, cached=cached, coverage="source_random_fanart_record")

    def source_status(self) -> dict:
        return self.client.source_status()
