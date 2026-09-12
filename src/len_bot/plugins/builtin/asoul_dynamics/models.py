"""The site's known JSON records and explicit native-tool arguments."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceMember(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class DynamicRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    dynamicId: str = Field(min_length=1)
    member: SourceMember
    type: str = Field(min_length=1)
    contentText: str
    publishedAt: AwareDatetime
    url: str = Field(min_length=1)


class DynamicPage(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[DynamicRecord]
    total: int = Field(ge=0)
    nextCursor: str | None


class HistoricalDayPage(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[DynamicRecord]


class FanartRecord(BaseModel):
    model_config = ConfigDict(extra="allow")
    sourceDynamicId: str = Field(min_length=1)
    sourceDynamicUrl: str = Field(min_length=1)
    contentType: str = Field(min_length=1)


class FanartPage(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[FanartRecord]
    total: int = Field(ge=0)
    nextCursor: str | None


class RandomFanartPage(BaseModel):
    model_config = ConfigDict(extra="allow")
    items: list[FanartRecord]


class LatestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member: str | None = Field(min_length=1)
    limit: int | None = Field(ge=1, le=50)


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str | None = Field(min_length=1)
    member: str | None = Field(min_length=1)
    cursor: str | None = Field(min_length=1)
    dynamic_type: str | None = Field(min_length=1)
    sort: Literal["newest", "oldest", "likes", "comments"] | None
    limit: int | None = Field(default=None, ge=1, le=50,
        description="null或省略使用配置页量；续页原样保留上次返回的实际页量")


class DetailRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dynamic_id: str = Field(min_length=1)


class CardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dynamic_id: str | None = Field(default=None, min_length=1)
    result_ref: str | None = Field(default=None, min_length=1)
    item_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def locator(self):
        if self.result_ref is not None and self.item_id is None:
            raise ValueError('result_ref requires item_id')
        if self.result_ref is None and self.dynamic_id is None:
            raise ValueError('provide result_ref + item_id or an obtained dynamic_id')
        return self


class FanartCardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_dynamic_id: str | None = Field(default=None, min_length=1)
    result_ref: str | None = Field(default=None, min_length=1)
    item_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode='after')
    def locator(self):
        if self.result_ref is not None and self.item_id is None:
            raise ValueError('result_ref requires item_id')
        if self.result_ref is None and self.source_dynamic_id is None:
            raise ValueError('provide result_ref + item_id or an obtained source_dynamic_id')
        return self


class OnThisDayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month_day: str | None
    limit: int | None = Field(ge=1, le=20)

    @field_validator("month_day")
    @classmethod
    def month_and_day(cls, value):
        if value is not None:
            if len(value) != 5:
                raise ValueError("month_day must use MM-DD")
            date.fromisoformat("2000-" + value)
        return value


class FanartFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    character: str | None = Field(min_length=1)
    content_type: Literal["image", "video", "text", "other"] | None
    category: str | None = Field(min_length=1)
    kind: str | None = Field(min_length=1)


class FanartSearchRequest(FanartFilter):
    query: str | None = Field(min_length=1)
    cursor: str | None = Field(min_length=1)
    sort: Literal["newest", "oldest"] | None = Field(default=None,
        description="null或省略使用配置排序；续页保留上次返回的实际排序")
    limit: int | None = Field(default=None, ge=1, le=48,
        description="null或省略使用配置页量；续页保留上次返回的实际页量")


class RandomFanartRequest(FanartFilter):
    pass
