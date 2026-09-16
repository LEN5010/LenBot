"""Fixed media worker input and source-located output; no executable arguments."""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SegmentSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    bvid: str = Field(pattern=r'^BV[A-Za-z0-9]{10}$')
    cid: int = Field(gt=0)
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    frames: int = Field(default=6, ge=0, le=12)
    audio: bool = False

    @model_validator(mode='after')
    def bounded(self):
        if not 0 < self.end_ms - self.start_ms <= 300_000:
            raise ValueError('媒体片段必须是明确递增的毫秒范围，单次不超过300秒')
        if self.frames == 0 and not self.audio:
            raise ValueError('至少选择抽帧或音频')
        return self


class MediaSegmentRequest(SegmentSelection):
    source_duration_ms: int = Field(gt=0)
    video_url: str | None = Field(default=None, max_length=8192)
    audio_url: str | None = Field(default=None, max_length=8192)
    max_download_bytes: int = Field(ge=1_048_576, le=200_000_000)
    max_source_pixels: int = Field(default=8_294_400, ge=1024, le=33_177_600)
    max_frame_dimension: int = Field(default=1280, ge=128, le=2048)

    @field_validator('video_url', 'audio_url')
    @classmethod
    def anonymous_resource(cls, value):
        if value is None:
            return value
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.fragment
                or parsed.port not in {None, 443}
                or not any(host == domain or host.endswith('.' + domain)
                           for domain in ('bilivideo.com', 'bilivideo.cn', 'bilivideo.net'))):
            raise ValueError('媒体只接受平台返回的 HTTPS B站资源域，实际目标仍须被网关出口策略允许')
        return value

    @model_validator(mode='after')
    def acquired_tracks(self):
        if self.end_ms > self.source_duration_ms:
            raise ValueError('选定时间范围超出原分P时长')
        if self.frames and self.video_url is None:
            raise ValueError('抽帧需要匿名可访问的视频轨')
        if self.audio and self.audio_url is None:
            raise ValueError('选定分P没有匿名可访问的音轨')
        return self


class SegmentFrame(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: str = Field(pattern=r'^frame_[0-9]{3}\.png$')
    source_time_ms: float = Field(ge=0, allow_inf_nan=False)
    timestamp_basis: Literal['decoder_pts_after_accurate_seek'] = 'decoder_pts_after_accurate_seek'


class SegmentManifest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    execution_id: str = Field(pattern=r'^x[0-9a-f]{32}$')
    bvid: str
    cid: int
    requested_start_ms: int
    requested_end_ms: int
    source_duration_ms: int
    status: Literal['ok', 'partial', 'error']
    frames: list[SegmentFrame] = Field(default_factory=list, max_length=12)
    audio_path: Literal['audio.wav'] | None = None
    audio_start_ms: int | None = None
    audio_end_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    downloaded_bytes: int = Field(ge=0)
    network_requests: int = Field(ge=0)
    error_code: str | None = None
    detail: str = Field(default='', max_length=2000)
    coverage: str = '仅列出的采样帧和音频区间；帧间画面与区间外内容未覆盖'
