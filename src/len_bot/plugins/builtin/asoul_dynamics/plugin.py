"""Specific site reads returned through LenBot's existing observation pipeline."""
from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginCallContext, PluginManifest, PluginPermission, PluginType
from len_bot.tools.results import ToolResult, ToolSource

from .client import DynamicsClient, SourceSnapshot
from .config import DynamicsConfig
from .models import DetailRequest, FanartSearchRequest, LatestRequest, OnThisDayRequest, RandomFanartRequest, SearchRequest

if TYPE_CHECKING:
    from len_bot.config_store import MemberSettings, TimeSettings


class AsoulDynamicsPlugin(BasePlugin):
    def __init__(self, *, config: DynamicsConfig, enabled: bool, time_settings: TimeSettings,
                 members: list[MemberSettings]):
        tools = ["get_asoul_dynamics", "search_asoul_dynamics", "read_asoul_dynamic",
                 "get_asoul_on_this_day", "search_asoul_fanart", "get_random_asoul_fanart"]
        super().__init__(PluginManifest(id="asoul_dynamics", name="A-SOUL 动态查询", version="1.0.0",
            description="读取该动态站已抓取的内容，提供历史同日与二创查询；不自动广播。",
            plugin_type=PluginType.TOOL, permissions=[PluginPermission.REGISTER_TOOL], enabled=enabled,
            timeout_seconds=config.tool_timeout_seconds, config=config.model_dump(),
            config_schema=DynamicsConfig.model_json_schema(), registered_tools=tools))
        self.config = config
        self.zone = ZoneInfo(time_settings.timezone)
        self.client = DynamicsClient(config, members)

    async def on_load(self, context: PluginContext):
        definitions = [
            ("get_asoul_dynamics", LatestRequest, self.get_latest, False,
             "读取该源已抓取的最新动态，按源publishedAt排序。member须为已配置成员/别名，null明确查全部；limit=null用已配置页量。图片仅给来源链接，未加载像素。"),
            ("search_asoul_dynamics", SearchRequest, self.search, True,
             "检索该源动态；query/member/cursor/dynamic_type为null时不加对应筛选，sort=null使用配置。继续分页须保留原筛选与排序并提交返回的cursor。未知成员失败，不改查全员。"),
            ("read_asoul_dynamic", DetailRequest, self.read_dynamic, True,
             "读取已取得且尚新鲜的源动态记录。没有详情接口；范围保持为源查询提供的内容，不能视作平台原动态全文或看过图片。"),
            ("get_asoul_on_this_day", OnThisDayRequest, self.on_this_day, True,
             "查询源站历史同日内容；month_day为MM-DD，null使用已配置业务时区的今天；limit=null使用配置页量。不自动播报。"),
            ("search_asoul_fanart", FanartSearchRequest, self.search_fanart, True,
             "按源站二创标签检索。query/character/content_type/category/kind/cursor的null均为不筛选；character是源标签（可含团体），不自动展开成员。排序/页量来自配置。"),
            ("get_random_asoul_fanart", RandomFanartRequest, self.random_fanart, True,
             "按明确源标签随机取一条二创；null为不筛选。每次执行是独立随机查询，仅返回观察，不主动发送；媒体链接不代表已看像素。"),
        ]
        for name, schema, handler, deferred, description in definitions:
            context.register_tool(name=name, description=description, parameters=schema.model_json_schema(),
                handler=handler, kind="read", roles=("conversation", "work"), deferred=deferred)

    async def on_unload(self):
        await self.client.close()

    def _result(self, snapshot: SourceSnapshot, *, cached: bool, coverage: str, data: dict | None = None) -> ToolResult:
        payload = {
            "scope": "该源已抓取的内容；不代表全平台绝对最新",
            "coverage": coverage,
            "fetched_at": snapshot.fetched_at,
            "source_url": snapshot.url,
            "pixels_loaded": False,
            "content_scope": "保留源查询返回的全部字段；未取得平台原动态详情，片段不扩写为全文。",
            "data": data if data is not None else snapshot.data,
        }
        items = payload["data"].get("items")
        status = "no_results" if items == [] else "ok"
        return ToolResult(status=status, content=json.dumps(payload, ensure_ascii=False),
            sources=[ToolSource(url=snapshot.url, title="A-SOUL 动态查询站")],
            fetched_at=snapshot.fetched_at, cached=cached, coverage=coverage, evidence_kind="external")

    async def get_latest(self, arguments: dict, call_context: PluginCallContext) -> ToolResult:
        request = LatestRequest.model_validate(arguments)
        limit = self.config.default_limit if request.limit is None else request.limit
        snapshot, cached = await self.client.search(query=None, member=request.member, cursor=None,
            dynamic_type=None, sort="newest", limit=limit)
        data = copy.deepcopy(snapshot.data)
        data["items"].sort(key=lambda item: datetime.fromisoformat(item["publishedAt"]), reverse=True)
        data["continuation_arguments"] = {
            "query": None, "member": request.member, "cursor": data["nextCursor"],
            "dynamic_type": None, "sort": "newest",
        } if data["nextCursor"] else None
        return self._result(snapshot, cached=cached, coverage="source_latest_query_records", data=data)

    async def search(self, arguments: dict, call_context: PluginCallContext) -> ToolResult:
        request = SearchRequest.model_validate(arguments)
        sort = self.config.search_sort if request.sort is None else request.sort
        snapshot, cached = await self.client.search(query=request.query, member=request.member,
            cursor=request.cursor, dynamic_type=request.dynamic_type, sort=sort, limit=self.config.default_limit)
        return self._result(snapshot, cached=cached, coverage="source_search_records")

    async def read_dynamic(self, arguments: dict, call_context: PluginCallContext) -> ToolResult:
        request = DetailRequest.model_validate(arguments)
        snapshot = self.client.read_obtained_dynamic(request.dynamic_id)
        return self._result(snapshot, cached=True, coverage="retrieved_source_record")

    async def on_this_day(self, arguments: dict, call_context: PluginCallContext) -> ToolResult:
        request = OnThisDayRequest.model_validate(arguments)
        month_day = request.month_day
        if month_day is None:
            month_day = datetime.fromtimestamp(call_context.now, self.zone).strftime("%m-%d")
        limit = self.config.on_this_day_default_limit if request.limit is None else request.limit
        snapshot, cached = await self.client.on_this_day(month_day, limit)
        return self._result(snapshot, cached=cached, coverage="source_historical_day_records")

    async def search_fanart(self, arguments: dict, call_context: PluginCallContext) -> ToolResult:
        request = FanartSearchRequest.model_validate(arguments)
        snapshot, cached = await self.client.fanart(query=request.query, character=request.character,
            content_type=request.content_type, category=request.category, kind=request.kind,
            cursor=request.cursor, random=False)
        return self._result(snapshot, cached=cached, coverage="source_fanart_search_records")

    async def random_fanart(self, arguments: dict, call_context: PluginCallContext) -> ToolResult:
        request = RandomFanartRequest.model_validate(arguments)
        snapshot, cached = await self.client.fanart(query=None, character=request.character,
            content_type=request.content_type, category=request.category, kind=request.kind,
            cursor=None, random=True)
        return self._result(snapshot, cached=cached, coverage="source_random_fanart_record")

    def source_status(self) -> dict:
        return self.client.source_status()
