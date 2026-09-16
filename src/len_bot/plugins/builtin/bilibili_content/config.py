from pydantic import BaseModel, ConfigDict, Field, field_validator


class BilibiliPluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    account_uid: int | None = Field(default=None, gt=0, description='运营确认的 B 站账号 UID；空则登录态能力不可用')
    authenticated_read_enabled: bool = Field(default=False, description='登录态动态读取；仍需独立 grant 和当前工作审查')
    sessdata: str = Field(json_schema_extra={'writeOnly': True})
    buvid3: str | None = Field(default=None, json_schema_extra={'writeOnly': True}, description='运营提供的真实设备 Cookie；点赞必需，不自动生成')
    daily_like_limit: int = Field(default=0, ge=0, le=100, description='同账号每日点赞/取消点赞写入上限；0 关闭')
    daily_favorite_limit: int = Field(default=0, ge=0, le=100, description='同账号每日收藏/取消收藏写入上限；0 关闭')
    allowed_collection_ids: list[int] = Field(default_factory=list, description='允许操作的本账号收藏夹完整 ID；空则不可收藏')
    bili_jct: str = Field(json_schema_extra={'writeOnly': True})
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    max_subtitle_bytes: int = Field(default=2 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024,
        title='字幕下载字节上限', description='流式读取正文的硬上限；超限明确失败，不保存残缺 JSON')

    @field_validator('allowed_collection_ids')
    @classmethod
    def valid_collections(cls, values):
        if any(value <= 0 for value in values) or len(values) != len(set(values)):
            raise ValueError('收藏夹 ID 必须为不重复的正整数')
        return values
