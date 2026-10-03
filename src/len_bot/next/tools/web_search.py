"""One explicitly configured public search service; no provider failover."""

from __future__ import annotations

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from html import unescape
from html.parser import HTMLParser
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field
from ..trials.replay_web import RecordedWeb

SEARCH_URL = "https://www.bing.com/search"
MAX_RESPONSE_BYTES = 1_000_000
ERROR_BYTES = 2048
PROMPT = Path(__file__).resolve().parents[2] / "prompts" / "next_web_search.md"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


class WebSearchSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    provider: Literal["bing_rss"]
    timeout_seconds: float = Field(default=15, gt=0, allow_inf_nan=False)
    max_results: int = Field(default=5, ge=1, le=20, strict=True)


class WebSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=500, pattern=r"\S")
    count: int | None = Field(default=None, ge=1, le=20, strict=True)


WEB_SEARCH_TOOL = {"type": "function", "function": {
    "name": "web_search",
    "description": "搜索公开网页，返回服务商给出的标题、链接、摘要与可用的发布日期；摘要用于定位来源，不等于已读取原文。",
    "parameters": WebSearchArguments.model_json_schema(),
}}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(raw: str) -> str:
    parser = _Text()
    parser.feed(raw)
    parser.close()
    return re.sub(r"\s+", " ", unescape("".join(parser.parts))).strip()


def _item(item: ET.Element, index: int, raw: bytes) -> dict[str, str | None]:
    title = _plain(item.findtext("title") or "")
    url = (item.findtext("link") or "").strip()
    snippet = _plain(item.findtext("description") or "")
    published_at = (item.findtext("pubDate") or "").strip() or None
    try:
        parts = urlsplit(url)
    except ValueError as error:
        raise ValueError(f"web_search RSS item {index} has invalid link {url[:200]!r}; "
                         f"response fragment: {raw[:500]!r}") from error
    if (not title or any(character.isspace() or ord(character) < 32 for character in url)
            or parts.scheme not in {"http", "https"} or not parts.netloc or parts.hostname is None
            or parts.username is not None or parts.password is not None):
        raise ValueError(f"web_search RSS item {index} has invalid title or link; response fragment: {raw[:500]!r}")
    return {"title": title, "url": url, "snippet": snippet, "published_at": published_at}


def _parse_rss(raw: bytes, max_results: int) -> list[dict[str, str | None]]:
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError(f"web_search RSS contains a DTD or entity; response fragment: {raw[:500]!r}")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise ValueError(f"web_search invalid RSS: {error}; response fragment: {raw[:500]!r}") from error
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError(f"web_search expected RSS channel; response fragment: {raw[:500]!r}")
    return [_item(item, index, raw) for index, item in enumerate(channel.findall("item")[:max_results], 1)]


async def execute_web_search(settings: WebSearchSettings, arguments: WebSearchArguments, *,
                             recording: RecordedWeb | None = None) -> str:
    count = arguments.count if arguments.count is not None else settings.max_results
    if count > settings.max_results:
        raise ValueError(f"web_search count {count} exceeds configured max_results {settings.max_results}")
    if recording is not None:
        item, body = recording.search(arguments.query)
        if item.status_code != 200:
            raise ValueError(f'web_search Bing RSS HTTP {item.status_code}: {body[:ERROR_BYTES].decode("utf-8", errors="replace")}')
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError(f'Frozen RSS exceeds {MAX_RESPONSE_BYTES} decoded bytes')
        result = {'provider': settings.provider, 'query': arguments.query, 'results': _parse_rss(body, count),
                  'recording': {'source': recording.data.source,
                                'fetched_at': datetime.fromtimestamp(item.fetched_at, timezone.utc).isoformat(),
                                'live_request': False}}
        return Template((PROMPT.parent / 'next_replay_web_search.md').read_text()).substitute(
            result=json.dumps(result, ensure_ascii=False))
    deadline = asyncio.timeout(settings.timeout_seconds)
    try:
        async with deadline:
            async with httpx.AsyncClient(
                timeout=settings.timeout_seconds, trust_env=False, follow_redirects=False,
                headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml"},
                transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
            ) as client:
                async with client.stream("GET", SEARCH_URL, params={"q": arguments.query, "format": "rss"}) as response:
                    body = bytearray()
                    limit = MAX_RESPONSE_BYTES if response.status_code == 200 else ERROR_BYTES
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(body) + len(chunk) > limit:
                            if response.status_code == 200:
                                raise ValueError(f"web_search RSS exceeds {MAX_RESPONSE_BYTES} decoded bytes")
                            body.extend(chunk[:limit - len(body)])
                            break
                        body.extend(chunk)
                    if response.status_code != 200:
                        raise ValueError(f"web_search Bing RSS HTTP {response.status_code}: "
                                         f"{body.decode('utf-8', errors='replace')}")
    except TimeoutError as error:
        if deadline.expired():
            raise TimeoutError(f"web_search exceeded {settings.timeout_seconds} seconds") from error
        raise
    rows = _parse_rss(bytes(body), count)
    result = {"provider": "bing_rss", "query": arguments.query, "results": rows}
    return Template(PROMPT.read_text()).substitute(result=json.dumps(result, ensure_ascii=False))
