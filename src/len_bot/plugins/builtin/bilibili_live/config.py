from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class LiveSceneConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    announcements: list[Literal['live_started']] = Field(description='本群开放的公告；空列表表示不发开播邀请')
    live_subscriptions: list[str] = Field(description='订阅成员的登记名称，来自共享成员目录')
    mention_all: bool = Field(description='开播邀请是否提及全体成员，仅作用于本插件公告')


class LivePluginConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    api_url: str = Field(min_length=1)
    interval_seconds: float = Field(ge=10)
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    max_age_seconds: float = Field(gt=0)
    source_timezone: str
    announcement_instructions: str = Field(min_length=1)
    announcement_max_steps: int = Field(ge=1)
    announcement_max_tool_calls: int = Field(ge=0)
    announcement_context_tokens: int = Field(gt=0)
    announcement_output_tokens: int = Field(gt=0)

    @model_validator(mode='after')
    def valid_clock_and_budget(self):
        try:
            ZoneInfo(self.source_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('source_timezone must name an available IANA timezone') from None
        if self.announcement_output_tokens >= self.announcement_context_tokens:
            raise ValueError('Announcement output reservation must be smaller than its context')
        return self
