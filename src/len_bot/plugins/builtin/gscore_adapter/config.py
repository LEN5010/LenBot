from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from urllib.parse import urlsplit


class GscoreConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ws_url: str = Field(min_length=1)
    access_token: str = Field(default='', json_schema_extra={'writeOnly': True})
    core_version: str | None = Field(default=None, max_length=120, description='运营核对的 Core 版本或提交；未填写表示未完成现场协议确认')
    core_bot_id: str = 'LenBot'
    platform_bot_id: str = 'onebot'
    bot_self_id: str = Field(min_length=1)
    token_query_parameter: str = 'token'
    connect_timeout_seconds: float = Field(gt=0, default=10.0)
    reconnect_seconds: float = Field(gt=0, default=5.0)
    command_timeout_seconds: float = Field(gt=0, default=20.0)
    command_prefix: str = '/gs'

    @model_validator(mode='after')
    def identities_are_explicit(self):
        url = urlsplit(self.ws_url)
        if url.scheme not in {'ws', 'wss'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Core 地址须为不含凭据、query 或 fragment 的 ws/wss 端点；令牌使用独立密钥字段')
        if self.platform_bot_id != 'onebot' or not self.bot_self_id.isdigit() or int(self.bot_self_id) <= 0:
            raise ValueError('Core 平台身份须为 onebot，bot_self_id 须为实际 QQ 号')
        if self.core_bot_id == self.platform_bot_id:
            raise ValueError('gscore_adapter.core_bot_id and platform_bot_id must be distinct identities')
        if not self.command_prefix.strip() or any(char.isspace() for char in self.command_prefix):
            raise ValueError('gscore_adapter.command_prefix must be one command prefix')
        return self


class GscoreSceneConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    command_enabled: bool = False
    allow_core_push: bool = False
    command_prefix: str | None = None

    @field_validator('command_prefix')
    @classmethod
    def exact_prefix(cls, value):
        if value is not None and (not value or any(char.isspace() for char in value)):
            raise ValueError('场景命令前缀必须是一个精确、无空白的词')
        return value
