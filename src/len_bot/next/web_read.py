"""Isolated public document read: one bounded fetch, one extraction, saved pages."""

from __future__ import annotations

import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from string import Template

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .pdf_read import read_pdf

from .config import WebReadSettings
from .http_read import fetch_public
from .store import Store, WebPage
from .replay_web import RecordedWeb


PAGE_CHARS = 4000
TEXT_BYTES = 2_000_000
PDF_BYTES = 10_000_000
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_web_read.md"


class WebReadArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    url: str | None = None
    document: int | None = Field(default=None, gt=0)
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def one_location(self) -> WebReadArguments:
        if (self.url is None) == (self.document is None):
            raise ValueError("exactly one of url or document is required")
        if self.url is not None:
            if not self.url.strip():
                raise ValueError("url must not be blank")
            if self.offset:
                raise ValueError("url first read requires offset=0; use document to continue")
        return self


WEB_READ_TOOL = {"type": "function", "function": {
    "name": "web_read",
    "description": "读取公开 HTTP(S) 网页、文本、JSON 或 PDF 文本层。url 首读保存完整实际提取结果并显示前4000字符；"
                   "document 用返回的文档编号和字符 offset 稳定续读，不重新联网。",
    "parameters": WebReadArguments.model_json_schema(),
}}


def _web_limit(media_type: str, prefix: bytes) -> int:
    return PDF_BYTES if media_type == "application/pdf" or prefix.startswith(b"%PDF-") else TEXT_BYTES


def _decode(body: bytes, content_type: str) -> str:
    header = Message()
    header["content-type"] = content_type
    encoding = header.get_content_charset() or "utf-8"
    return body.decode(encoding, errors="strict")


async def _extract_html(body: str, url: str) -> str:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", __name__, "extract",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate(
            json.dumps({"html": body, "url": url}, ensure_ascii=False).encode("utf-8"),
        )
    except BaseException:
        if process.returncode is None:
            process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise ValueError(f"web_read HTML extraction failed: {stderr.decode(errors='replace')[-500:]}")
    return stdout.decode("utf-8", errors="strict")


async def _extract(url: str, content_type: str, body: bytes) -> tuple[str, str, str]:
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/pdf" or body.startswith(b"%PDF-"):
        document = await read_pdf(body)
        notice = (f"PDF文本层：共{document['page_count']}页，实际提取{document['pages_extracted']}页；"
                  f"20万字符上限{'已达到，仅保证已提取范围' if document['truncated'] else '未达到'}；"
                  "不含OCR、图像或可靠的图表版式。")
        if not document["has_text"]:
            notice += " 页面未提取到文字，可能是扫描页；需查看原图。"
        return "application/pdf", document["text"], notice
    if not media_type:
        raise ValueError("web_read response has no Content-Type")
    if media_type in {"text/html", "application/xhtml+xml"}:
        decoded = _decode(body, content_type)
        content = await _extract_html(decoded, url)
        if not content.strip():
            raise ValueError("web_read HTML extraction returned no body")
        return media_type, content, "HTML正文由trafilatura一次提取；脚本渲染和未提取区域不在此结果内，图片链接不代表已看过像素。"
    if media_type in {"text/plain", "text/markdown", "text/csv"}:
        decoded = _decode(body, content_type)
        return media_type, decoded, "按响应字符集（缺省UTF-8）严格解码的文本；未执行页面脚本。"
    if media_type in {"application/json", "text/json"} or media_type.endswith("+json"):
        decoded = _decode(body, content_type)
        try:
            json.loads(decoded, parse_constant=_reject_json_constant,
                       parse_float=str, parse_int=str)
        except ValueError as error:
            raise ValueError(f"web_read invalid JSON: {error}; response fragment: {decoded[:500]}") from error
        return media_type, decoded, "已校验JSON语法，正文保留响应原文与数字写法。"
    raise ValueError(f"web_read unsupported Content-Type: {media_type}")


def _page(document: int, page: WebPage, offset: int) -> str:
    total = len(page.content)
    if offset > total:
        raise ValueError(f"web_read offset {offset} exceeds document length {total}")
    end = min(total, offset + PAGE_CHARS)
    result = {
        "document": document, "url": page.url, "final_url": page.final_url,
        "fetched_at": datetime.fromtimestamp(page.fetched_at, timezone.utc).isoformat(),
        "media_type": page.media_type, "notice": page.notice,
        "offset": offset, "end": end, "total_chars": total,
        "next_offset": end if end < total else None,
        "text": page.content[offset:end],
    }
    return Template(PROMPT.read_text()).substitute(result=json.dumps(result, ensure_ascii=False))


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


async def execute_web_read(store: Store, scene: str, settings: WebReadSettings,
                           arguments: WebReadArguments, *, recording: RecordedWeb | None = None) -> str:
    if arguments.document is not None:
        page = store.web_page(scene, arguments.document)
        if page is None:
            raise ValueError(f"当前场景没有网页文档 {arguments.document}")
        return _page(arguments.document, page, arguments.offset)
    deadline = asyncio.timeout(settings.timeout_seconds)
    try:
        async with deadline:
            if recording is None:
                final_url, content_type, body = await fetch_public(arguments.url, settings.timeout_seconds, _web_limit)
                fetched_at = time.time()
            else:
                item, body = recording.document(arguments.url)
                if not 200 <= item.status_code < 300:
                    raise ValueError(f'HTTP read status {item.status_code}: {body[:2048].decode("utf-8", errors="replace")}')
                final_url, content_type, fetched_at = item.final_url, item.content_type, item.fetched_at
                media_type = content_type.split(';', 1)[0].strip().lower()
                if len(body) > _web_limit(media_type, body[:8]):
                    raise ValueError(f'Frozen web_read response exceeds the normal {media_type} byte limit')
            media_type, content, notice = await _extract(final_url, content_type, body)
            if recording is not None:
                notice += " 本次读取的是冻结响应，不是实时联网查询；抓取时间来自原始资料清单。"
            page = WebPage(url=arguments.url, final_url=final_url, fetched_at=fetched_at,
                           media_type=media_type, content=content, notice=notice)
            document = store.save_web_page(scene, page)
    except TimeoutError as error:
        if deadline.expired():
            raise TimeoutError(f"web_read exceeded {settings.timeout_seconds} seconds") from error
        raise
    return _page(document, page, 0)


def _worker() -> None:
    import trafilatura

    request = json.loads(sys.stdin.buffer.read())
    content = trafilatura.extract(
        request["html"], url=request["url"], output_format="markdown",
        include_links=True, include_tables=True, include_images=True,
        with_metadata=True,
    )
    if content is None or not content.strip():
        raise ValueError("trafilatura returned no readable article body")
    sys.stdout.buffer.write(content.encode("utf-8"))


if __name__ == "__main__":
    if sys.argv[1:] != ["extract"]:
        raise SystemExit("web_read worker expects extract")
    try:
        _worker()
    except Exception as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1)
