"""Transport-neutral observations; successful retrieval does not establish truth."""
from __future__ import annotations

import time
import re
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict, Field, ValidationError, computed_field, model_validator


ToolErrorStage = Literal['availability', 'arguments', 'references', 'execution', 'presentation', 'commit']


def error_source_url(value: str) -> str:
    """Keep the source address without userinfo, query credentials or fragments."""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return ''
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return ''
    host = parsed.hostname
    if ':' in host:
        host = '[' + host + ']'
    if port is not None:
        host += ':' + str(port)
    return urlunsplit((parsed.scheme, host, parsed.path, '', ''))


def error_message(value: str) -> str:
    """Redact transport credentials from diagnostic prose, not source bodies."""
    text = re.sub(r'https?://[^\s<>\"\']+', lambda match: error_source_url(match.group()), value)
    text = re.sub(r'(?i)\b(Bearer|Basic)\s+[^\s,;]+', r'\1 [redacted]', text)
    return re.sub(r'(?i)\b(authorization|cookie|set-cookie|sessdata|api[_-]?key|access[_-]?token|password)\s*[:=]\s*[^\r\n]+',
                  r'\1: [redacted]', text)


class ToolFieldError(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    loc: list[str | int]
    type: str
    message: str


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
    tool_name: str | None = None
    tool_call_id: str | None = None
    error_stage: ToolErrorStage | None = None
    error_details: list[ToolFieldError] = Field(default_factory=list)
    http_status: int | None = None
    correction: dict[str, Any] | None = None
    evidence_kind: Literal["external", "retrieval", "model", "unknown"] = "unknown"

    @computed_field
    @property
    def evidence_span(self) -> dict[str, Any] | None:
        span = self.displayed_range
        if (not self.result_id or span is None or span.start == span.end
                or self.status not in {'ok', 'partial', 'no_results'} or 'locator' in self.coverage):
            return None
        return {'result_id': self.result_id, 'coordinate_unit': self.coordinate_unit,
                'start': span.start, 'end': span.end}

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
    def failure(cls, message: str, code: str = "tool_error", *, stage: ToolErrorStage | None = None,
                tool_name: str | None = None, tool_call_id: str | None = None,
                details: list[ToolFieldError] | None = None, http_status: int | None = None,
                sources: list[ToolSource] | None = None) -> ToolResult:
        return cls(status="error", content=f"Error: {error_message(message)}", error_code=code,
            error_stage=stage, tool_name=tool_name, tool_call_id=tool_call_id,
            error_details=details or [], http_status=http_status, sources=sources or [])

    @classmethod
    def validation_failure(cls, error: ValidationError, *, tool_name: str | None = None,
                           tool_call_id: str | None = None, stage: ToolErrorStage = 'arguments',
                           code: str = 'invalid_arguments') -> ToolResult:
        details = [ToolFieldError(loc=list(item['loc']), type=item['type'], message=error_message(item['msg']))
                   for item in error.errors(include_url=False, include_context=False, include_input=False)]
        explanation = '; '.join(f"{'.'.join(map(str, item.loc)) or '$'}: {item.message}" for item in details)
        return cls.failure(explanation, code, stage=stage,
            tool_name=tool_name, tool_call_id=tool_call_id, details=details)

    def error_context(self, tool_name: str, tool_call_id: str | None, *, stage: ToolErrorStage = 'execution') -> ToolResult:
        if self.status not in {'error', 'unsupported'}:
            return self
        return self.model_copy(update={'tool_name': self.tool_name or tool_name,
            'tool_call_id': self.tool_call_id or tool_call_id,
            'error_stage': self.error_stage or ('arguments' if self.error_code == 'invalid_arguments' else stage),
            'content': error_message(self.content),
            'error_details': [detail.model_copy(update={'message': error_message(detail.message)}) for detail in self.error_details],
            'sources': [source.model_copy(update={'url': error_source_url(source.url)}) if source.url else source
                        for source in self.sources]})
