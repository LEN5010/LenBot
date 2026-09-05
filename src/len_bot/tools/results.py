"""Transport-neutral observations; successful retrieval does not establish truth."""
from __future__ import annotations

import json
import time
from typing import Literal, Any
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
    fetched_at: float = Field(default_factory=time.time)
    result_id: str | None = None
    observation_event_id: str | None = None
    truncated: bool = False
    next_offset: int | None = None
    cached: bool = False
    coverage: str = "unknown"
    error_code: str | None = None
    evidence_kind: Literal["external", "retrieval", "model", "unknown"] = "unknown"

    def __str__(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def __contains__(self, value: str) -> bool:
        # Direct legacy plugin callers used substring checks on returned text.
        return value in str(self)

    def page(self, offset: int = 0, limit: int = 6000) -> ToolResult:
        if offset < 0 or limit < 1 or limit > 12000:
            raise ValueError("offset must be nonnegative; limit must be 1..12000")
        end = min(len(self.content), offset + limit)
        return self.model_copy(update={
            "content": self.content[offset:end],
            "truncated": self.truncated or end < len(self.content),
            "next_offset": end if end < len(self.content) else None,
        })

    @classmethod
    def normalize(cls, value: Any) -> ToolResult:
        if isinstance(value, cls):
            return value.model_copy(deep=True)
        if isinstance(value, str):
            try:
                data = json.loads(value)
                if isinstance(data, dict) and "status" in data and "content" in data:
                    return cls.model_validate(data)
            except (ValueError, TypeError):
                pass
            return cls(content=value)  # No error/absence guessing from legacy prose.
        return cls(content=json.dumps(value, ensure_ascii=False, default=str))

    @classmethod
    def failure(cls, message: str, code: str = "tool_error") -> ToolResult:
        return cls(status="error", content=f"Error: {message}", error_code=code)
