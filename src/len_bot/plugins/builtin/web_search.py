"""Web Search Tool plugin (ADR-0021, V2 plan §十六 Plugin 2).

Cognition-invoked agentic tools over DuckDuckGo's HTML endpoint (no API key).
Tool results are plain observation strings — the plugin never prompts an LLM
and never touches outbound messaging (Invariant B).
"""

import logging
import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.plugins.models import PluginManifest, PluginPermission, PluginType
from len_bot.tools.retrieval import RetrievalToolkit
from len_bot.plugins.net_policy import validate_url

logger = logging.getLogger(__name__)

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

_RESULT_ANCHOR = re.compile(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL)
_RESULT_SNIPPET = re.compile(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.DOTALL)
_TAG_STRIP = re.compile(r"<[^>]+>")


def _strip_tags(raw: str) -> str:
    return unescape(re.sub(r"\s+", " ", _TAG_STRIP.sub("", raw))).strip()


def _resolve_ddg_href(href: str) -> str:
    """DDG wraps result URLs in /l/?uddg=<encoded>; unwrap to the real target."""
    if "//duckduckgo.com/l/" in href or "duckduckgo.com/l/" in href:
        parsed = urlparse(href if href.startswith("http") else f"https:{href}")
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target else href
    if href.startswith("//"):
        return f"https:{href}"
    return href


class WebSearchToolPlugin(BasePlugin):
    def __init__(self):
        super().__init__(manifest=PluginManifest(
            id="web_search_tool",
            name="实时联网认知检索",
            description="Pi 主动调用的网页搜索与页面阅读工具（DuckDuckGo，免 API Key）。",
            version="1.0.0",
            plugin_type=PluginType.TOOL,
            permissions=[PluginPermission.REGISTER_TOOL],
            config_schema={
                "type": "object",
                "properties": {
                    "max_results": {"type": "integer", "title": "最大结果数", "minimum": 1, "maximum": 10}
                }
            },
            default_config={"max_results": 5},
            registered_tools=["web_search", "read_page"],
        ))
        self._client = httpx.AsyncClient(
            timeout=15.0,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )

    async def on_load(self, context: PluginContext) -> None:
        context.register_tool(
            name="web_search",
            description="联网搜索：输入查询关键词，返回网页标题、链接与摘要。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"}
                },
                "required": ["query"]
            },
            handler=self._web_search,
        )
        context.register_tool(
            name="read_page",
            description="读取网页正文文本：输入 URL，返回截断后的页面文字内容。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "要读取的网页地址"}
                },
                "required": ["url"]
            },
            handler=self._read_page,
        )

    async def on_unload(self) -> None:
        await self._client.aclose()

    async def _web_search(self, args: dict[str, Any]) -> str:
        query = str(args.get("query", "")).strip()
        if not query:
            return "Error: web_search requires a 'query' argument."
        max_results = int(self.manifest.config.get("max_results", 5))

        resp = await self._client.get(DDG_HTML_URL, params={"q": query})
        resp.raise_for_status()
        html = resp.text

        anchors = _RESULT_ANCHOR.findall(html)
        snippets = [_strip_tags(s) for s in _RESULT_SNIPPET.findall(html)]

        lines = []
        for idx, (href, title_html) in enumerate(anchors[:max_results]):
            title = _strip_tags(title_html)
            url = _resolve_ddg_href(href)
            snippet = snippets[idx] if idx < len(snippets) else ""
            lines.append(f"{idx + 1}. {title}\n   URL: {url}\n   摘要: {snippet}")
        if not lines:
            return "未搜索到相关结果。"
        # Marker semantics shared with retrieval tools (ADR-0020)
        return RetrievalToolkit._with_complexity_signal(lines, high_hit_count=max_results)

    async def _read_page(self, args: dict[str, Any]) -> str:
        url = str(args.get("url", "")).strip()
        if not url.startswith(("http://", "https://")):
            return "Error: read_page requires an absolute http(s) URL."

        # SSRF Guard (ADR-0030, §21.4)
        allowed, reason = validate_url(url)
        if not allowed:
            logger.warning("SSRF blocked read_page attempt for %s: %s", url, reason)
            return f"[安全拦截: 目标地址受限 - {reason}]"

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            text = _strip_tags(resp.text)
            if not text:
                return "页面无可提取文本。"
            return text[:3000]
        except Exception as e:
            return f"读取页面失败: {e}"
