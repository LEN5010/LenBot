"""Anonymous source resolution; stable video identity stays separate from CDN URLs."""
from __future__ import annotations

import hashlib
import time
from pathlib import PurePosixPath
from urllib.parse import quote, urlencode, urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field

from len_bot.media.segment_protocol import MediaSegmentRequest, SegmentSelection


class PublicResponse(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    code: int
    data: dict | None = None


class Page(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    cid: int
    duration: int = Field(gt=0)


class Video(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    bvid: str
    pages: list[Page]


class Track(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    baseUrl: str
    mimeType: str
    codecs: str
    bandwidth: int = Field(gt=0)


class Dash(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    video: list[Track] | None = None
    audio: list[Track] | None = None


class PlayData(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    dash: Dash


class WbiImages(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    img_url: str
    sub_url: str


class Navigation(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    wbi_img: WbiImages


class BilibiliSegmentSource:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False,
            headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})

    async def close(self):
        await self.client.aclose()

    async def _json(self, path, params=None, *, anonymous_navigation=False):
        async with self.client.stream('GET', 'https://api.bilibili.com' + path, params=params) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 1_048_576:
                    raise ValueError('B站元信息超过读取上限')
                body.extend(chunk)
        value = PublicResponse.model_validate_json(body)
        allowed = {0, -101} if anonymous_navigation else {0}
        if value.code not in allowed or value.data is None:
            raise ValueError(f'B站匿名元信息读取失败，code={value.code}')
        return value.data

    async def resolve(self, selection: SegmentSelection, max_bytes: int) -> MediaSegmentRequest:
        video = Video.model_validate(await self._json('/x/web-interface/view', {'bvid': selection.bvid}))
        page = next((item for item in video.pages if item.cid == selection.cid), None)
        if video.bvid != selection.bvid or page is None or selection.end_ms > page.duration * 1000:
            raise ValueError('片段身份或范围与平台分P元信息不一致')
        nav = Navigation.model_validate(await self._json('/x/web-interface/nav', anonymous_navigation=True))
        keys = [PurePosixPath(urlsplit(value).path).stem for value in (nav.wbi_img.img_url, nav.wbi_img.sub_url)]
        if any(len(key) != 32 or any(c not in '0123456789abcdef' for c in key) for key in keys):
            raise ValueError('平台 Wbi 密钥格式不符合已核对协议')
        combined = ''.join(keys)
        permutation = (46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13)
        mixin = ''.join(combined[i] for i in permutation)
        params = {'bvid': selection.bvid, 'cid': selection.cid, 'fnval': 16, 'fnver': 0,
                  'qn': 32, 'gaia_source': 'view-card', 'wts': round(time.time())}
        query = urlencode(sorted((k, ''.join(c for c in str(v) if c not in "!'()*"))
                                 for k, v in params.items()), quote_via=quote)
        params['w_rid'] = hashlib.md5((query + mixin).encode()).hexdigest()
        data = PlayData.model_validate(await self._json('/x/player/wbi/playurl', params))
        videos = [x for x in data.dash.video or [] if x.mimeType == 'video/mp4' and x.codecs.startswith('avc1')]
        audios = [x for x in data.dash.audio or [] if x.mimeType == 'audio/mp4' and x.codecs.startswith('mp4a')]
        return MediaSegmentRequest(**selection.model_dump(), source_duration_ms=page.duration * 1000,
            video_url=min(videos, key=lambda x: x.bandwidth).baseUrl if selection.frames and videos else None,
            audio_url=min(audios, key=lambda x: x.bandwidth).baseUrl if selection.audio and audios else None,
            max_download_bytes=max_bytes)
