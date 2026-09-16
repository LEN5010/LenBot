"""Read public Bilibili content through explicit native work tools.

Cognition-invoked agentic tools for querying public Bilibili video details,
search results, and dynamic feeds.
Tools return observations and never directly message users or trigger tasks.
"""

import logging
import json
from urllib.parse import urlencode
from len_bot.tools.results import ObservationProvenance, ToolNextCall, ToolResult, ToolSource, error_source_url
from typing import Any, Optional
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.plugins.base import BasePlugin, PluginContext
from .config import BilibiliPluginConfig
from len_bot.plugins.models import PluginCallContext
from len_bot.plugins.net_policy import validate_url
from ..bilibili_client import public_json

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


class SubtitleResourceLimit(ValueError):
    """The source could not be acquired within the configured byte contract."""


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


class VideoPagesArguments(VideoInfoArguments):
    pass


class VideoCommentsArguments(VideoInfoArguments):
    page: int = Field(default=1, ge=1, description="评论页码，从 1 开始，按 source_next_call 续读")


class VideoSubtitlesArguments(VideoInfoArguments):
    cid: int | None = Field(default=None, gt=0, description="分 P 的 cid；省略时用第一 P")
    start_ms: int = Field(default=0, ge=0, description="字幕起始毫秒")
    end_ms: int | None = Field(default=None, ge=0, description="字幕结束毫秒；空表示读到本段末尾")

    @model_validator(mode='after')
    def increasing_range(self):
        if self.end_ms is not None and self.end_ms <= self.start_ms:
            raise ValueError('end_ms 必须晚于 start_ms；范围为 [start_ms,end_ms)')
        return self


class VideoPage(BaseModel):
    cid: int = Field(gt=0)
    page: int = Field(ge=1)
    part: str
    duration: int = Field(ge=0)


class VideoView(BaseModel):
    bvid: str
    aid: int
    pages: list[VideoPage] = Field(default_factory=list)
    pubdate: int | None = None


class SubtitleTrack(BaseModel):
    id: int | None = None
    lan: str
    lan_doc: str
    subtitle_url: str


class SubtitleTracks(BaseModel):
    subtitles: list[SubtitleTrack] = Field(default_factory=list)


class PlayerInfo(BaseModel):
    need_login_subtitle: bool = False
    subtitle: SubtitleTracks = Field(default_factory=SubtitleTracks)


class SubtitleCue(BaseModel):
    start: float = Field(alias='from', ge=0, allow_inf_nan=False)
    end: float = Field(alias='to', ge=0, allow_inf_nan=False)
    content: str

    @model_validator(mode='after')
    def increasing(self):
        if self.end < self.start:
            raise ValueError('字幕时间轴逆序')
        return self


class SubtitleDocument(BaseModel):
    body: list[SubtitleCue]


class CommentPage(BaseModel):
    num: int = Field(ge=1)
    size: int = Field(gt=0)
    count: int = Field(ge=0)


class CommentsResponse(BaseModel):
    page: CommentPage
    replies: list[dict] | None = None


class BilibiliContentPlugin(BasePlugin):
    """Tool plugin providing Bilibili public content retrieval capabilities."""

    def __init__(self, context: PluginContext):
        super().__init__(context.manifest)
        self.config: BilibiliPluginConfig = context.config
        self._client: Optional[httpx.AsyncClient] = None
        self._public_client: Optional[httpx.AsyncClient] = None

    async def on_load(self, context: PluginContext) -> None:
        headers = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"}
        self._public_client = httpx.AsyncClient(timeout=self.config.request_timeout_seconds,
            trust_env=False, headers=headers.copy(), follow_redirects=False)
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
        context.register_tool(
            name="get_video_pages", description="读取 B 站视频分 P 与 cid 时间轴，不下载视频。",
            parameter_model=VideoPagesArguments, handler=self._get_video_pages,
            purpose="读取视频分P", aliases=("视频分P", "分P列表"),
            keywords=("分P", "cid", "时长", "B站"),
            kind="read", roles=("conversation", "work"), deferred=True,
        )
        context.register_tool(
            name="get_video_comments", description="按页读取 B 站视频评论；返回评论者观点，不是视频正文。",
            parameter_model=VideoCommentsArguments, handler=self._get_video_comments,
            purpose="读取视频评论", aliases=("视频评论",),
            keywords=("评论", "B站", "弹幕不是评论"),
            kind="read", roles=("conversation", "work"), deferred=True,
        )
        context.register_tool(
            name="get_video_subtitles", description="读取指定分 P 的字幕时间段；没有字幕时明确失败，不下载视频。",
            parameter_model=VideoSubtitlesArguments, handler=self._get_video_subtitles,
            purpose="读取视频字幕", aliases=("视频字幕", "字幕时间轴"),
            keywords=("字幕", "时间轴", "B站"),
            kind="read", roles=("conversation", "work"), deferred=True,
        )

    async def on_unload(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._public_client:
            await self._public_client.aclose()
            self._public_client = None

    async def _query(self, url: str, params: dict[str, Any], *, content_key: str | None = None,
                     account: bool = False) -> ToolResult:
        if account and url != 'https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space':
            raise ValueError('账号客户端不能访问未登记的 API 目的地')
        source = ToolSource(url=url + "?" + urlencode(params))
        try:
            data = await public_json(self._client if account else self._public_client, url, params)
        except ValueError as error:
            return ToolResult.failure(str(error), "upstream_error")
        payload = data["data"]
        records = payload[content_key] if content_key else payload
        return ToolResult(status='ok' if records else 'no_results',
            content=json.dumps(payload, ensure_ascii=False), sources=[source], evidence_kind="external",
            coverage="api_response", provenance=ObservationProvenance(access='account' if account else 'anonymous_public'))

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
                                 {"host_mid": args.mid}, content_key="items", account=True)

    async def _view(self, args: VideoInfoArguments) -> VideoView:
        params = {'bvid': args.bvid} if args.bvid is not None else {'aid': args.aid}
        body = await public_json(self._public_client, "https://api.bilibili.com/x/web-interface/view", params)
        return VideoView.model_validate(body['data'])

    async def _get_video_pages(self, args: VideoPagesArguments, call_context: PluginCallContext) -> ToolResult:
        try:
            data = await self._view(args)
        except ValueError as error:
            return ToolResult.failure(str(error), "upstream_error")
        pages = data.pages
        source = ToolSource(url=f'https://www.bilibili.com/video/{data.bvid}')
        if not pages:
            return ToolResult(status="no_results", content="该视频没有分 P 记录。", sources=[source], evidence_kind="external")
        records = [{"cid": item.cid, "page": item.page, "part": item.part,
                    "duration_seconds": item.duration} for item in pages]
        return ToolResult(content=json.dumps({"bvid": data.bvid, "aid": data.aid,
            "pages": records, "coverage": "video_pages"}, ensure_ascii=False),
            sources=[source], evidence_kind="external", coverage="api_response",
            provenance=ObservationProvenance(access='anonymous_public'))

    async def _get_video_comments(self, args: VideoCommentsArguments, call_context: PluginCallContext) -> ToolResult:
        try:
            data = await self._view(args)
        except ValueError as error:
            return ToolResult.failure(str(error), "upstream_error")
        oid = data.aid
        result = await self._query("https://api.bilibili.com/x/v2/reply", {
            "type": 1, "oid": oid, "pn": args.page, "ps": 20, "sort": 2,
        }, content_key="replies")
        if result.status not in {"ok", "partial", "no_results"}:
            return result
        try:
            payload = CommentsResponse.model_validate_json(result.content)
        except ValueError as error:
            return ToolResult.failure(str(error), 'upstream_error', sources=result.sources)
        more = payload.page.num * payload.page.size < payload.page.count
        continuation = ToolNextCall(name='get_video_comments', arguments={
            'bvid': data.bvid, 'page': payload.page.num + 1}) if more else None
        wrapped = {
            "bvid": data.bvid, "aid": oid, "page": payload.page.model_dump(),
            "replies": payload.replies or [],
            "coverage": "video_comments",
            "note": "这些是评论者观点，不是视频正文、字幕或平台结论。",
        }
        return result.model_copy(update={"content": json.dumps(wrapped, ensure_ascii=False),
                                         "coverage": "video_comments", 'source_next_call': continuation})

    async def _get_video_subtitles(self, args: VideoSubtitlesArguments, call_context: PluginCallContext) -> ToolResult:
        try:
            data = await self._view(args)
        except ValueError as error:
            return ToolResult.failure(str(error), "upstream_error")
        pages = data.pages
        page = next((item for item in pages if item.cid == args.cid), None) if args.cid else (pages[0] if pages else None)
        if args.cid is not None and page is None:
            return ToolResult.failure('cid 不属于本视频的分 P；请先读取 get_video_pages', 'invalid_cid', stage='arguments')
        if page is None:
            return ToolResult(status="no_results", content="没有可用的分 P cid，无法读取字幕。",
                evidence_kind="external")
        source = ToolSource(url=f'https://www.bilibili.com/video/{data.bvid}?p={page.page}', title=page.part)
        try:
            player = await public_json(self._public_client, "https://api.bilibili.com/x/player/wbi/v2", {
                "bvid": data.bvid, "cid": page.cid,
            })
            player = PlayerInfo.model_validate(player['data'])
        except ValueError as error:
            return ToolResult.failure(str(error), "upstream_error", sources=[source])
        if player.need_login_subtitle:
            return ToolResult(status='unsupported', error_code='authentication_required', sources=[source],
                content='该视频字幕需要登录；匿名读取未取得字幕，不表示没有字幕。', evidence_kind='external')
        tracks = player.subtitle.subtitles
        if not tracks:
            return ToolResult(status="no_results",
                content="本次匿名响应未提供字幕轨道；不能据此说视频没有内容。", sources=[source], evidence_kind="external")
        track = next((item for item in tracks if item.lan in {"zh-CN", "ai-zh", "zh-Hans"}), tracks[0])
        subtitle_url = track.subtitle_url
        if subtitle_url.startswith("//"):
            subtitle_url = "https:" + subtitle_url
        if not subtitle_url.startswith("https://"):
            return ToolResult.failure("字幕轨道没有可用的 HTTPS 地址", "upstream_error")
        try:
            body = await self._subtitle_cues(subtitle_url, args.start_ms, args.end_ms)
        except SubtitleResourceLimit as error:
            return ToolResult.failure(str(error), 'resource_limit', sources=[source,
                ToolSource(url=error_source_url(subtitle_url), title=track.lan_doc)])
        except ValueError as error:
            return ToolResult.failure(str(error), "upstream_error", sources=[source])
        return ToolResult(content=json.dumps({
            "bvid": data.bvid, "cid": page.cid, 'page': page.page, "start_ms": args.start_ms, "end_ms": args.end_ms,
            'published_at': data.pubdate, 'track_id': track.id, "lan": track.lan, "lan_doc": track.lan_doc,
            "cues": body["cues"], "cue_count": body["cue_count"],
            "truncated": body["truncated"],
            'downloaded_bytes': body['downloaded_bytes'],
            "coverage": "subtitle_time_range",
            "note": "字幕是该时间段的识别文本，不是视频画面，也不下载视频。",
        }, ensure_ascii=False), evidence_kind="external", coverage="subtitle_time_range",
            sources=[source, ToolSource(url=error_source_url(subtitle_url), title=track.lan_doc)],
            provenance=ObservationProvenance(access='anonymous_public'))

    async def _subtitle_cues(self, url: str, start_ms: int, end_ms: int | None) -> dict:
        allowed, reason = validate_url(url)
        if not allowed:
            raise ValueError(f"安全拦截: {reason}")
        chunks = bytearray()
        # This client has no account credentials, and identity encoding keeps
        # the byte cap a cap on both downloaded and decoded JSON bytes.
        async with self._public_client.stream('GET', url, headers={'Accept-Encoding': 'identity'}) as response:
            response.raise_for_status()
            if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                raise SubtitleResourceLimit('字幕资源忽略 identity 编码，不能在此路径内核对解码字节上限')
            async for chunk in response.aiter_raw():
                if len(chunks) + len(chunk) > self.config.max_subtitle_bytes:
                    raise SubtitleResourceLimit(f'字幕正文超过 {self.config.max_subtitle_bytes} 字节，未保存残缺正文')
                chunks.extend(chunk)
        try:
            document = SubtitleDocument.model_validate_json(chunks)
        except ValueError as error:
            raise ValueError('字幕正文不符合时间轴 JSON 合同') from error
        start_s = start_ms / 1000
        end_s = None if end_ms is None else end_ms / 1000
        cues = []
        for item in document.body:
            begin, finish = item.start, item.end
            if finish <= start_s:
                continue
            if end_s is not None and begin >= end_s:
                continue
            cues.append({"from_ms": int(begin * 1000), "to_ms": int(finish * 1000),
                         "content": item.content})
        return {"cues": cues, "cue_count": len(cues), "truncated": False, 'downloaded_bytes': len(chunks)}
