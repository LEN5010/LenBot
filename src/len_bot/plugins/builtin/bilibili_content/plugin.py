"""Read public Bilibili content through explicit native work tools.

Cognition-invoked agentic tools for querying public Bilibili video details,
search results, and dynamic feeds.
Tools return observations and never directly message users or trigger tasks.
"""

import logging
import json
from urllib.parse import urlencode
from len_bot.tools.results import ToolResult, ToolSource
from typing import Any, Optional
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.plugins.base import BasePlugin, PluginContext
from .config import BilibiliPluginConfig
from len_bot.plugins.models import PluginCallContext
from len_bot.plugins.net_policy import validate_url

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class VideoInfoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra={"oneOf": [
        {"required": ["bvid"], "properties": {"bvid": {"type": "string"}, "aid": {"type": "null"}}},
        {"required": ["aid"], "properties": {"aid": {"type": "integer"}, "bvid": {"type": "null"}}},
    ]})
    bvid: str | None = Field(default=None, pattern=r"^BV[A-Za-z0-9]{10}$", description="完整 BV 号，与 aid 二选一")
    aid: int | None = Field(default=None, gt=0, description="正整数 AV 号，与 bvid 二选一")

    @model_validator(mode="after")
    def one_identifier(self):
        if (self.bvid is None) == (self.aid is None):
            raise ValueError("get_video_info 必须且只能提供 bvid 或 aid 之一")
        return self


class BilibiliSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    keyword: str = Field(min_length=1, pattern=r"\S", description="视频搜索关键词")
    page_size: int = Field(default=5, ge=1, le=10, description="返回条目数")


class DynamicFeedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mid: int = Field(gt=0, description="UP 主的正整数 UID")


class BilibiliContentPlugin(BasePlugin):
    """Tool plugin providing Bilibili public content retrieval capabilities."""

    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.config: BilibiliPluginConfig = context.config
        self._client: Optional[httpx.AsyncClient] = None

    async def on_load(self, context: PluginContext) -> None:
        headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
        sessdata = self.config.sessdata
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata};"

        self._client = httpx.AsyncClient(
            timeout=self.config.request_timeout_seconds, trust_env=False,
            headers=headers,
            follow_redirects=False,
        )

        context.register_tool(
            name="get_video_info",
            description="通过完整 bvid（如 BV17x411w7KC）或 aid 查询标题、简介、UP 主及播放互动数据；两个标识只能提供一个。",
            parameter_model=VideoInfoArguments, handler=self._get_video_info,
            purpose="读取 B 站视频详情", aliases=("B站视频详情", "视频信息"),
            keywords=("视频", "BV", "AV", "标题", "简介", "UP主", "播放数据"),
            kind="read", roles=("conversation", "work"), deferred=True,
        )
        context.register_tool(
            name="search_bilibili", description="输入搜索关键词，获取相关 B 站视频列表及播放数据。",
            parameter_model=BilibiliSearchArguments, handler=self._search_bilibili,
            purpose="搜索 B 站视频", aliases=("B站搜索", "搜索视频"),
            keywords=("视频", "哔哩哔哩", "B站", "搜索", "检索"),
            kind="read", roles=("conversation", "work"), deferred=True,
        )
        context.register_tool(
            name="get_dynamic_feed", description="按 UP 主 UID 查询平台动态接口；需要已配置有效 SESSDATA。",
            parameter_model=DynamicFeedArguments, handler=self._get_dynamic_feed,
            purpose="读取 B 站 UP 主最新动态", aliases=("UP主动态", "B站动态"),
            keywords=("动态", "UP主", "最新", "B站", "UID"),
            kind="read", roles=("conversation", "work"), deferred=True,
        )

    async def on_unload(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _query(self, url: str, params: dict[str, Any], *, content_key: str | None = None) -> ToolResult:
        allowed, reason = validate_url(url)
        if not allowed:
            return ToolResult.failure(f"安全拦截: {reason}", "blocked")
        source = ToolSource(url=url + "?" + urlencode(params))
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if data["code"] != 0:
            return ToolResult.failure(f"B站接口返回错误: {data['message']}", str(data["code"]))
        payload = data["data"]
        records = payload[content_key] if content_key else payload
        if not records:
            return ToolResult(status="no_results", content="接口未返回记录，不能推断现实不存在。", sources=[source], evidence_kind="external")
        return ToolResult(content=json.dumps(payload, ensure_ascii=False), sources=[source], evidence_kind="external", coverage="api_response")

    async def _get_video_info(self, args: VideoInfoArguments, call_context: PluginCallContext) -> ToolResult:
        params = args.model_dump(exclude_none=True)
        return await self._query("https://api.bilibili.com/x/web-interface/view", params)

    async def _search_bilibili(self, args: BilibiliSearchArguments, call_context: PluginCallContext) -> ToolResult:
        keyword = args.keyword
        return await self._query("https://api.bilibili.com/x/web-interface/search/type", {
            "search_type": "video", "keyword": keyword,
            "page_size": args.page_size,
        }, content_key="result")

    async def _get_dynamic_feed(self, args: DynamicFeedArguments, call_context: PluginCallContext) -> ToolResult:
        if not self.config.sessdata:
            return ToolResult(status="unsupported", content="查询动态需要配置有效 SESSDATA；当前未查询。", error_code="credentials_missing")
        return await self._query("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                                 {"host_mid": args.mid}, content_key="items")
