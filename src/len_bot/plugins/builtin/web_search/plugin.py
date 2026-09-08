"""Public Bing search and document reads; observations only, no cognition or sends."""

import logging
import re
from html import unescape
from html.parser import HTMLParser
from pydantic import BaseModel, ConfigDict, Field
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

import httpx
import asyncio
import json
import trafilatura

from len_bot.plugins.base import BasePlugin, PluginContext
from len_bot.config_store import SearchPluginConfig
from len_bot.plugins.models import PluginCallContext, PluginManifest, PluginPermission, PluginType
from len_bot.plugins.net_policy import validate_url
from len_bot.tools.http import fetch_public
from len_bot.tools.pdf_reader import MAX_PDF_BYTES, read_pdf
from len_bot.tools.results import ToolResult, ToolSource

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.bing.com/search"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

_TAG_STRIP = re.compile(r"<[^>]+>")


def _strip_tags(raw: str) -> str:
    return unescape(re.sub(r"\s+", " ", _TAG_STRIP.sub("", raw))).strip()


def _query_terms(query: str) -> list[str]:
    """Extract conservative lexical anchors for rejecting unrelated RSS rows."""
    cleaned = re.sub(r"(?i)\bsite:[^\s]+", " ", query)
    terms: list[str] = []
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]*|[\u4e00-\u9fff]+", cleaned):
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            if len(token) == 1:
                terms.append(token)
            else:
                terms.extend(token[index:index + 2] for index in range(len(token) - 1))
        else:
            terms.append(token.casefold())
    return list(dict.fromkeys(term for term in terms if term not in {"or", "and", "not"}))


def _query_sites(query: str) -> list[str]:
    return list(dict.fromkeys(site.casefold().strip('.') for site in re.findall(r"(?i)\bsite:([A-Za-z0-9.-]+)", query)))


def _relevance_score(query: str, title: str, snippet: str, url: str) -> int:
    terms = _query_terms(query)
    parsed = urlparse(url)
    # Exclude the URL query string: search providers may echo the user's
    # query there even when the result body is unrelated.
    haystack = f"{title} {snippet} {parsed.netloc} {parsed.path}".casefold()
    return sum(term.casefold() in haystack for term in terms)


class _MediaLinks(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self.base_url, self.images, self.pdfs = base_url, [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        raw = (attrs.get('src') or attrs.get('data-src')) if tag == 'img' else attrs.get('href') if tag == 'a' else None
        if not raw:
            return
        url = urljoin(self.base_url, raw)
        if urlparse(url).scheme not in {'http', 'https'}:
            return
        if tag == 'img' and len(self.images) < 24 and url not in [row['url'] for row in self.images]:
            self.images.append({'url': url, 'label': (attrs.get('alt') or '')[:200]})
        elif tag == 'a' and (urlparse(url).path.endswith('.pdf') or '/pdf/' in urlparse(url).path):
            if url not in self.pdfs and len(self.pdfs) < 12:
                self.pdfs.append(url)


class WebSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    query: str = Field(min_length=1, max_length=500, pattern=r"\S", description="搜索关键词；可包含 site: 官方域名限定")


class ReadPageArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str = Field(pattern=r"^https?://[^\s/?#]+(?:[/?#][^\s]*)?$", description="完整 HTTP(S) 网页、文本或 PDF 地址")


class WebSearchToolPlugin(BasePlugin):
    def __init__(self, *, config: dict, enabled: bool):
        super().__init__(manifest=PluginManifest(
            id="web_search_tool",
            name="实时联网认知检索",
            description="通过 Bing 公开检索查找网页，读取正文、PDF文本并保留图表入口，无需单独配置密钥。",
            version="1.1.0",
            timeout_seconds=config["tool_timeout_seconds"],
            plugin_type=PluginType.TOOL,
            permissions=[PluginPermission.REGISTER_TOOL],
            config_schema=SearchPluginConfig.model_json_schema(),
            config=config, enabled=enabled,
            registered_tools=["web_search", "read_page"],
        ))
        self._client = httpx.AsyncClient(
            timeout=config["request_timeout_seconds"], trust_env=False,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=False,
        )

    async def on_load(self, context: PluginContext) -> None:
        context.register_tool(
            name="web_search",
            description="通过Bing联网搜索，返回标题、链接与摘要。保留问题中的公司、型号与时间；摘要用于定位原始来源，可在query中使用site:限定官方站点。",
            parameter_model=WebSearchArguments,
            purpose="搜索公开网页与来源", aliases=("联网搜索", "网页搜索", "上网查"),
            keywords=("搜索", "联网", "网页", "资料", "官方", "查询"),
            handler=self._web_search,
            kind="read", roles=("work",),
        )
        context.register_tool(
            name="read_page",
            description="读取网页、文本、JSON或PDF文本，保留图表和PDF链接。长内容用read_tool_result续读；图表数值或PDF页面排版用read_web_media查看原图。需登录或仅脚本渲染的页面可能不可读。",
            parameter_model=ReadPageArguments,
            purpose="读取网页与 PDF 正文", aliases=("读网页", "读PDF", "读取链接"),
            keywords=("网页", "原文", "正文", "链接", "PDF", "文档"),
            handler=self._read_page,
            kind="read", roles=("work",),
        )

    async def on_unload(self) -> None:
        await self._client.aclose()

    async def _web_search(self, args: WebSearchArguments, call_context: PluginCallContext) -> ToolResult:
        query = args.query
        max_results = self.manifest.config["max_results"]

        resp = await self._client.get(SEARCH_URL, params={"q": query, "format": "rss"})
        resp.raise_for_status()
        try:
            if resp.status_code != 200 or len(resp.text) > 1_000_000 or '<!DOCTYPE' in resp.text.upper() or '<!ENTITY' in resp.text.upper():
                raise ValueError('Unexpected search response')
            root = ET.fromstring(resp.text)
            if root.tag != 'rss' or root.find('channel') is None:
                raise ValueError('Search response is not RSS')
        except (ET.ParseError, ValueError):
            return ToolResult(status='unsupported', error_code='search_unavailable', evidence_kind='external',
                content='搜索服务未返回可读取结果，可能是服务限制或验证页面；本次无法核实，不能判断对象不存在。')
        lines, sources = [], []
        sites = _query_sites(query)
        candidates = []
        for index, item in enumerate(root.findall('./channel/item')):
            title, url = _strip_tags(item.findtext('title') or ''), item.findtext('link') or ''
            if urlparse(url).scheme not in {'http', 'https'}:
                continue
            host = (urlparse(url).hostname or '').casefold().strip('.')
            if sites and not any(host == site or host.endswith('.' + site) for site in sites):
                continue
            snippet = _strip_tags(item.findtext('description') or '')
            candidates.append((_relevance_score(query, title, snippet, url), index, title, url, snippet))
        terms = _query_terms(query)
        required = 1 if len(terms) <= 2 else 2
        candidates = [row for row in candidates if row[0] >= required]
        candidates.sort(key=lambda row: (-row[0], row[1]))
        for _, _, title, url, snippet in candidates[:max_results]:
            lines.append(f"{len(lines) + 1}. {title}\n   URL: {url}\n   摘要: {snippet}")
            sources.append(ToolSource(url=url, title=title))
        if not lines:
            return ToolResult(status='no_results', content='此次检索没有与原查询相符的结果；保留原对象，不能推断现实不存在。', evidence_kind='external')
        return ToolResult(content="\n".join(lines), sources=sources, evidence_kind="external", coverage="search_snippets")

    async def _read_page(self, args: ReadPageArguments, call_context: PluginCallContext) -> ToolResult:
        url = args.url

        # Apply the existing allowed public-network URL policy before reading.
        allowed, reason = validate_url(url)
        if not allowed:
            logger.warning("SSRF blocked read_page attempt for %s: %s", url, reason)
            return ToolResult.failure(f"安全拦截: 目标地址受限 - {reason}", "blocked")

        final_url, headers, body = await fetch_public(self._client, url, max_bytes=MAX_PDF_BYTES)
        media_type = headers.get("content-type", "").split(";")[0].lower()
        source = ToolSource(url=final_url)
        if media_type.startswith('image/'):
            return ToolResult(status='unsupported', content='资源是图片，当前正文工具没有采用像素或登记可发送资产；请把来源URL交read_web_media读取原图。',
                sources=[source], evidence_kind='external', error_code='image_requires_media_reader', coverage='image_url_only')
        if media_type == 'application/pdf' or body.startswith(b'%PDF-'):
            document = await read_pdf(body)
            content = (f"PDF共{document['page_count']}页，提取到第{document['pages_extracted']}页；以下为文本层，图表与列布局需用read_web_media指定页码核对。\n"
                       + ('文本层为空，请查看页面原图。\n' if not document['has_text'] else '')
                       + document['text'])
            return ToolResult(content=content, sources=[source], evidence_kind='external',
                coverage='pdf_text' if document['has_text'] else 'pdf_text_empty', truncated=document['truncated'],
                status='partial' if document['truncated'] or not document['has_text'] else 'ok')
        if len(body) > 2_000_000:
            raise ValueError('网页或文本超过2MB上限')
        # aiter_bytes already decoded Content-Encoding; retain charset only.
        decoded = httpx.Response(200, headers={"content-type": headers.get("content-type", "")}, content=body).text
        if media_type in {"application/json", "text/json"} or media_type.endswith("+json"):
            content = json.dumps(json.loads(decoded), ensure_ascii=False, indent=2)
        elif media_type in {"text/plain", "text/markdown", "text/csv"}:
            content = decoded
        elif media_type in {"text/html", "application/xhtml+xml", ""}:
            extracted = await asyncio.to_thread(trafilatura.bare_extraction, decoded, url=final_url,
                include_links=True, include_tables=True, include_comments=True, with_metadata=True)
            if not extracted or not extracted.text or not extracted.text.strip():
                return ToolResult(status="unsupported", content="未提取到正文；可能需登录或脚本渲染，不能据此判定页面事实。",
                                  sources=[source], error_code="body_unavailable", evidence_kind="external")
            # Markdown preserves links and paragraph boundaries in the model-facing body.
            content = await asyncio.to_thread(trafilatura.extract, decoded, url=final_url,
                output_format="markdown", include_links=True, include_tables=True, include_comments=True)
            content = content or extracted.text
            resources = _MediaLinks(final_url)
            resources.feed(decoded)
            if resources.images or resources.pdfs:
                content = ('页面资源（最多24张图、12个PDF链接，尚未查看像素；指标图用read_web_media读取）：'
                           + json.dumps({'images': resources.images, 'pdfs': resources.pdfs}, ensure_ascii=False)
                           + '\n\n' + content)
            source.title = extracted.title or ""
            source.published_at = extracted.date
        else:
            return ToolResult(status="unsupported", content=f"尚未支持的内容类型: {media_type}", sources=[source], error_code="content_type")
        if not content.strip():
            return ToolResult(status="no_results", content="资源内容为空，事实仍未确认。", sources=[source], evidence_kind="external")
        return ToolResult(content=content, sources=[source], evidence_kind="external", coverage="retrieved_document")
