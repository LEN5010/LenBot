"""Parameters for the one configured dynamics site; no runtime defaults."""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DynamicsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True,
        json_schema_extra={'x-lenbot-enum-labels': {
            'search_sort': {'newest': '最新发布', 'oldest': '最早发布',
                            'likes': '按点赞数', 'comments': '按评论数'},
            'on_this_day_sort': {'hot': '按热度', 'likes': '按点赞数', 'comments': '按评论数'},
            'fanart_sort': {'newest': '最新创作', 'oldest': '最早创作'}}})

    api_base_url: str = Field(min_length=1)
    request_timeout_seconds: float = Field(gt=0)
    tool_timeout_seconds: float = Field(gt=0)
    cache_ttl_seconds: float = Field(ge=0)
    on_this_day_cache_ttl_seconds: float = Field(ge=0)
    default_limit: int = Field(ge=1, le=48)
    on_this_day_default_limit: int = Field(ge=1, le=20)
    search_sort: Literal["newest", "oldest", "likes", "comments"] = Field(
        title='默认搜索排序', description='查询动态时的默认排序；调用方没有指定排序时生效')
    on_this_day_sort: Literal["hot", "likes", "comments"] = Field(
        title='那年今日排序', description='历史同日动态的默认排序；调用方没有指定排序时生效')
    fanart_sort: Literal["newest", "oldest"] = Field(
        title='二创图排序', description='二创图查询的默认排序；调用方没有指定排序时生效')
    user_agent: str = Field(min_length=1)

    @field_validator("api_base_url")
    @classmethod
    def absolute_http_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("Dynamics API base must be an absolute HTTP(S) URL without query or fragment")
        return value.rstrip("/")
