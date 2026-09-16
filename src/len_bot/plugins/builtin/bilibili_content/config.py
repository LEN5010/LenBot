from pydantic import BaseModel, ConfigDict, Field


class BilibiliPluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    sessdata: str = Field(json_schema_extra={'writeOnly': True})
    bili_jct: str = Field(json_schema_extra={'writeOnly': True})
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    max_subtitle_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024,
        title='字幕下载字节上限', description='流式读取正文的硬上限；超限明确失败，不保存残缺 JSON')
