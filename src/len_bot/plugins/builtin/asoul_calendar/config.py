"""Explicit calendar source, command words and local rendering resources."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CalendarCommand = Literal["calendar_today", "calendar_tomorrow", "calendar_week"]


class CalendarConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_url: str = Field(min_length=1)
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    cache_ttl_seconds: float = Field(ge=0)
    user_agent: str = Field(min_length=1)
    commands: dict[CalendarCommand, list[str]]
    live_keywords: list[str]
    non_live_keywords: list[str]
    include_all_day: bool
    font_path: str = Field(min_length=1)
    image_width: int = Field(ge=640)
    avatar_paths: dict[str, str]

    @field_validator("source_url")
    @classmethod
    def calendar_source(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Calendar source must be an absolute HTTP(S) URL")
        return value

    @model_validator(mode="after")
    def distinct_command_words(self):
        seen = set()
        for words in self.commands.values():
            for word in words:
                if not word or word != word.strip() or word in seen:
                    raise ValueError("Calendar command words must be nonempty, trimmed and unique")
                seen.add(word)
        return self
