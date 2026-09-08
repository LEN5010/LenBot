"""Parameters for the one configured dynamics site; no runtime defaults."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DynamicsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    api_base_url: str = Field(min_length=1)
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    cache_ttl_seconds: float = Field(ge=0)
    on_this_day_cache_ttl_seconds: float = Field(ge=0)
    default_limit: int = Field(ge=1, le=48)
    on_this_day_default_limit: int = Field(ge=1, le=20)
    search_sort: Literal["newest", "oldest", "likes", "comments"]
    on_this_day_sort: Literal["hot", "likes", "comments"]
    fanart_sort: Literal["newest", "oldest"]
    user_agent: str = Field(min_length=1)

    @field_validator("api_base_url")
    @classmethod
    def absolute_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Dynamics API base must be an absolute HTTP(S) URL without query or fragment")
        return value.rstrip("/")
