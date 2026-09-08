"""Transport-neutral observations; successful retrieval does not establish truth."""
from __future__ import annotations

import time
from typing import Literal
from pydantic import BaseModel, Field


class ToolSource(BaseModel):
    url: str = ""
    title: str = ""
    published_at: str | None = None
    event_id: str | None = None


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
        end = min(len(self.content), offset + limit)
        return self.model_copy(update={
            "content": self.content[offset:end],
            "truncated": self.truncated or end < len(self.content),
            "next_offset": end if end < len(self.content) else None,
        })

    @classmethod
    def failure(cls, message: str, code: str = "tool_error") -> ToolResult:
        return cls(status="error", content=f"Error: {message}", error_code=code)
