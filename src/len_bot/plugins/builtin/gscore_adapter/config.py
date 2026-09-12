from pydantic import BaseModel, ConfigDict, Field, model_validator


class GscoreConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ws_url: str = Field(min_length=1)
    access_token: str = ''
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
        if self.core_bot_id == self.platform_bot_id:
            raise ValueError('gscore_adapter.core_bot_id and platform_bot_id must be distinct identities')
        if not self.command_prefix.strip() or ' ' in self.command_prefix:
            raise ValueError('gscore_adapter.command_prefix must be one command prefix')
        return self


class GscoreSceneConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    command_enabled: bool = False
    allow_core_push: bool = False
    command_prefix: str | None = None
