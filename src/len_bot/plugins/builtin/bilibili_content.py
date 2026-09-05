"""Bilibili Content Tool Plugin (ADR-0021 and ADR-0030).

Cognition-invoked agentic tools for querying public Bilibili video details,
search results, and dynamic feeds.
Tools only return observation strings and never directly message users or trigger tasks.
"""

import logging
import json
from urllib.parse import urlencode
from len_bot.tools.results import ToolResult, ToolSource
from typing import Any, Optional
import httpx

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginManifest, PluginPermission, PluginType
from len_bot.plugins.net_policy import validate_url

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class BilibiliContentPlugin(BasePlugin):
    """Tool plugin providing Bilibili public content retrieval capabilities."""

    def __init__(self):
        super().__init__(manifest=PluginManifest(
            id="bilibili_content",
            name="哔哩哔哩内容查询工具",
            description="机器人需要时主动查询哔哩哔哩公开视频、搜索结果和用户动态。",
            version="1.0.0",
            timeout_seconds=15.0,
            plugin_type=PluginType.TOOL,
            permissions=[PluginPermission.REGISTER_TOOL],
            config_schema={
                "type": "object",
                "properties": {
                    "sessdata": {"type": "string", "title": "登录凭据（SESSDATA，用于高级查询）"},
                    "bili_jct": {"type": "string", "title": "请求校验值（bili_jct）"},
                }
            },
            default_config={"sessdata": "", "bili_jct": ""},
            registered_tools=["get_video_info", "search_bilibili", "get_dynamic_feed"],
        ))
        self._client: Optional[httpx.AsyncClient] = None

    async def on_load(self, context: PluginContext) -> None:
        headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
        sessdata = self.manifest.config.get("sessdata", "")
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata};"

        self._client = httpx.AsyncClient(
            timeout=10.0,
            headers=headers,
            follow_redirects=False,
        )

        context.register_tool(
            name="get_video_info",
            description="获取B站视频详情：通过 bvid (如 BV1xx411c7X) 或 aid 查询标题、简介、UP主及播放互动数据。",
            parameters={
                "type": "object",
                "properties": {
                    "bvid": {"type": "string", "description": "视频 BV 号，如 BV1xx411c7X"},
                    "aid": {"type": "integer", "description": "视频 AV 号 (与 bvid 二选一)"},
                }
            },
            handler=self._get_video_info,
            read_only=True, deferred=True,
        )

        context.register_tool(
            name="search_bilibili",
            description="搜索B站视频：输入搜索关键词，获取相关视频列表及播放数据。",
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "搜索关键词"},
                    "page_size": {"type": "integer", "description": "返回条目数 (默认 5)"}
                },
                "required": ["keyword"]
            },
            handler=self._search_bilibili,
            read_only=True, deferred=True,
        )

        context.register_tool(
            name="get_dynamic_feed",
            description="获取UP主最新动态：输入 UP主 uid (mid)，查询其最新发布的动态内容。",
            parameters={
                "type": "object",
                "properties": {
                    "mid": {"type": "integer", "description": "UP主 UID (host_mid)"},
                },
                "required": ["mid"]
            },
            handler=self._get_dynamic_feed,
            read_only=True, deferred=True,
        )

    async def on_unload(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _query(self, url, params, *, content_key=None):
        allowed, reason = validate_url(url)
        if not allowed:
            return ToolResult.failure(f"安全拦截: {reason}", "blocked")
        source = ToolSource(url=url + "?" + urlencode(params))
        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                return ToolResult.failure(f"B站接口返回错误: {data.get('message', '')}", str(data.get("code")))
            payload = data.get("data") or {}
            records = payload.get(content_key) if content_key else payload
            if not records:
                return ToolResult(status="no_results", content="接口未返回记录，不能推断现实不存在。", sources=[source], evidence_kind="external")
            return ToolResult(content=json.dumps(payload, ensure_ascii=False), sources=[source], evidence_kind="external", coverage="api_response")
        except Exception as error:
            return ToolResult.failure(f"B站查询失败: {error}", type(error).__name__)

    async def _get_video_info(self, args):
        params = {key: args[key] for key in ("bvid", "aid") if args.get(key)}
        if not params:
            return ToolResult.failure("get_video_info 需要 bvid 或 aid", "invalid_arguments")
        return await self._query("https://api.bilibili.com/x/web-interface/view", params)

    async def _search_bilibili(self, args):
        keyword = str(args.get("keyword") or "").strip()
        if not keyword:
            return ToolResult.failure("search_bilibili 需要 keyword", "invalid_arguments")
        return await self._query("https://api.bilibili.com/x/web-interface/search/type", {
            "search_type": "video", "keyword": keyword,
            "page_size": min(max(1, int(args.get("page_size", 5))), 10),
        }, content_key="result")

    async def _get_dynamic_feed(self, args):
        if not args.get("mid"):
            return ToolResult.failure("get_dynamic_feed 需要 mid", "invalid_arguments")
        if not self.manifest.config.get("sessdata"):
            return ToolResult(status="unsupported", content="查询动态需要配置有效 SESSDATA；当前未查询。", error_code="credentials_missing")
        return await self._query("https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
                                 {"host_mid": args["mid"]}, content_key="items")
