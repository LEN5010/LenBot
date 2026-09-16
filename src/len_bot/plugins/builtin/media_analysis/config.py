from pydantic import BaseModel, ConfigDict, Field

from len_bot.cognition.providers import TranscriptionProfile


class MediaAnalysisConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    image_ref: str = Field(default='media', pattern=r'^[a-z][a-z0-9_-]{0,63}$', title='媒体镜像引用')
    network_policy: str = Field(default='public', pattern=r'^[a-z][a-z0-9_-]{0,63}$', title='公共出口策略')
    max_download_bytes: int = Field(ge=1_048_576, le=200_000_000, title='单片段实际下载字节上限')
    timeout_seconds: float = Field(gt=30, le=3600, title='工具总期限（秒）')
    transcription: TranscriptionProfile | None = Field(default=None, title='可选音频转写绑定',
        description='使用已配置供应商；须核对 verbose_json 和分段时间戳能力与费用，默认不配置')
