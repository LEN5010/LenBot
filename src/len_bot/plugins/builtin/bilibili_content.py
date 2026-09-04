"""Bilibili Content Tool Plugin (ADR-0021 and ADR-0030).

Cognition-invoked agentic tools for querying public Bilibili video details,
search results, and dynamic feeds.
Tools only return observation strings and never directly message users or trigger tasks.
"""

import logging
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
            description="供认知模型主动调用的 B站公开视频信息、搜索与动态检索工具。",
            version="1.0.0",
            plugin_type=PluginType.TOOL,
            permissions=[PluginPermission.REGISTER_TOOL],
            config_schema={
                "type": "object",
                "properties": {
                    "sessdata": {"type": "string", "title": "SESSDATA (用户 Cookie，用于高级检索)"},
                    "bili_jct": {"type": "string", "title": "bili_jct (CSRF Token)"},
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
        )

    async def on_unload(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _get_video_info(self, args: dict[str, Any]) -> str:
        bvid = str(args.get("bvid") or "").strip()
        aid = args.get("aid")
        if not bvid and not aid:
            return "错误: get_video_info 需要提供 bvid 或 aid 参数。"

        url = "https://api.bilibili.com/x/web-interface/view"
        allowed, reason = validate_url(url)
        if not allowed:
            return f"[安全拦截: 请求受限 - {reason}]"

        params = {}
        if bvid:
            params["bvid"] = bvid
        if aid:
            params["aid"] = aid

        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                return f"获取视频信息失败: {data.get('message', '未知错误')} (code={data.get('code')})"

            d = data.get("data", {})
            owner = d.get("owner", {}).get("name", "未知")
            stat = d.get("stat", {})
            return (
                f"【视频信息】\n"
                f"标题: {d.get('title')}\n"
                f"UP主: {owner}\n"
                f"BV号: {d.get('bvid')}\n"
                f"播放: {stat.get('view', 0)} · 弹幕: {stat.get('danmaku', 0)} · 点赞: {stat.get('like', 0)}\n"
                f"简介: {d.get('desc', '')[:200]}"
            )
        except Exception as e:
            logger.warning("Bilibili get_video_info error: %s", e)
            return f"获取B站视频信息异常: {e}"

    async def _search_bilibili(self, args: dict[str, Any]) -> str:
        keyword = str(args.get("keyword") or "").strip()
        if not keyword:
            return "错误: search_bilibili 需要提供 keyword 参数。"
        page_size = min(int(args.get("page_size", 5)), 10)

        url = "https://api.bilibili.com/x/web-interface/search/type"
        allowed, reason = validate_url(url)
        if not allowed:
            return f"[安全拦截: 请求受限 - {reason}]"

        try:
            resp = await self._client.get(url, params={
                "search_type": "video",
                "keyword": keyword,
                "page_size": page_size
            })
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                return f"搜索B站视频失败: {data.get('message', '未知错误')} (需配置 SESSDATA 凭据)"

            results = data.get("data", {}).get("result", [])
            if not results:
                return f"未搜索到关键词 '{keyword}' 的相关B站视频。"

            lines = [f"B站搜索 '{keyword}' 结果:"]
            for idx, item in enumerate(results[:page_size]):
                title = str(item.get("title", "")).replace("<em class=\"keyword\">", "").replace("</em>", "")
                author = item.get("author", "未知")
                bvid = item.get("bvid", "")
                play = item.get("play", 0)
                lines.append(f"{idx + 1}. {title}\n   UP: {author} | 播放: {play} | https://www.bilibili.com/video/{bvid}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Bilibili search_bilibili error: %s", e)
            return f"B站搜索异常: {e}"

    async def _get_dynamic_feed(self, args: dict[str, Any]) -> str:
        mid = args.get("mid")
        if not mid:
            return "错误: get_dynamic_feed 需要提供 mid 参数。"

        url = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"
        allowed, reason = validate_url(url)
        if not allowed:
            return f"[安全拦截: 请求受限 - {reason}]"

        sessdata = self.manifest.config.get("sessdata", "")
        if not sessdata:
            return "提示: 查询 UP 主动态需在插件配置中填入有效的 SESSDATA Cookie。"

        try:
            resp = await self._client.get(url, params={"host_mid": mid})
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                return f"获取UP主动态失败: {data.get('message', '未知错误')}"

            items = data.get("data", {}).get("items", [])
            if not items:
                return f"UP主 (UID: {mid}) 暂无公开动态。"

            lines = [f"UP主 (UID: {mid}) 最近动态:"]
            for idx, item in enumerate(items[:3]):
                desc_text = item.get("modules", {}).get("module_dynamic", {}).get("desc", {}).get("text", "")
                lines.append(f"{idx + 1}. {desc_text[:150]}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning("Bilibili get_dynamic_feed error: %s", e)
            return f"获取UP主动态异常: {e}"
