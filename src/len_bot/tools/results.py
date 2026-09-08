"""Transport-neutral observations; successful retrieval does not establish truth."""
from __future__ import annotations

import time
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolSource(BaseModel):
    url: str = ""
    title: str = ""
    published_at: str | None = None
    event_id: str | None = None


class ToolNextCall(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1)
    arguments: dict[str, Any]


class DisplayedRange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    total: int = Field(ge=0)

    @model_validator(mode='after')
    def ordered(self):
        if not self.start <= self.end <= self.total:
            raise ValueError('Displayed range must be within the saved body')
        return self


class ToolResult(BaseModel):
    status: Literal["ok", "no_results", "partial", "error", "unsupported"] = "ok"
    content: str = ""
    sources: list[ToolSource] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    fetched_at: float = Field(default_factory=time.time)
    result_id: str | None = None
    observation_event_id: str | None = None
    truncated: bool = False
    next_offset: int | None = None
    coordinate_unit: Literal['characters', 'records'] = 'characters'
    displayed_range: DisplayedRange | None = None
    next_call: ToolNextCall | None = None
    source_next_call: ToolNextCall | None = None
    source_truncated: bool = False
    cached: bool = False
    duration_ms: float | None = None
    coverage: str = "unknown"
    error_code: str | None = None
    evidence_kind: Literal["external", "retrieval", "model", "unknown"] = "unknown"

    def __str__(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def page(self, offset: int, limit: int) -> ToolResult:
        if offset < 0 or limit < 1:
            raise ValueError("offset must be nonnegative and limit must be positive")
        if offset > len(self.content):
            raise ValueError('offset exceeds the saved observation body')
        end = min(len(self.content), offset + limit)
        continuation = (ToolNextCall(name='read_tool_result',arguments={
            'result_id':self.result_id,'offset':end,'limit':limit,'coordinate_unit':'characters'})
            if self.result_id and end < len(self.content) else None)
        source_truncated = self.source_truncated or (self.truncated and self.displayed_range is None)
        return self.model_copy(update={
            "content": self.content[offset:end],
            "truncated": source_truncated or end < len(self.content),
            "next_offset": end if end < len(self.content) else None,
            'coordinate_unit':'characters',
            'displayed_range':DisplayedRange(start=offset,end=end,total=len(self.content)),
            'next_call':continuation,
            'source_next_call':self.source_next_call if offset == 0 and end == len(self.content) else None,
            'source_truncated':source_truncated,
        })

    @classmethod
    def failure(cls, message: str, code: str = "tool_error") -> ToolResult:
        return cls(status="error", content=f"Error: {message}", error_code=code)
