"""Bounded browser commands shared by host, Gateway and container stdin/stdout."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from len_bot.browser.models import BrowserOpenInput, BrowserPageInput, BrowserInteractInput, BrowserCaptureInput

MAX_BROWSER_FRAME_BYTES = 16_000_000
COMMAND_ARGUMENTS = {'open': BrowserOpenInput, 'snapshot': BrowserPageInput,
                     'interact': BrowserInteractInput, 'capture': BrowserCaptureInput}


class BrowserCommand(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    command_id: str = Field(pattern=r'^cmd_[a-f0-9]{32}$')
    native_call_id: str = Field(min_length=1, max_length=300)
    scene_id: str = Field(min_length=1, max_length=200)
    job_id: str = Field(min_length=1, max_length=200)
    job_revision: int = Field(ge=1)
    operation: Literal['open', 'snapshot', 'interact', 'capture']
    arguments: BrowserOpenInput | BrowserPageInput | BrowserInteractInput | BrowserCaptureInput

    @model_validator(mode='before')
    @classmethod
    def parse_operation(cls, value):
        if not isinstance(value, dict) or value.get('operation') not in COMMAND_ARGUMENTS:
            raise ValueError('未知浏览器命令')
        values = dict(value)
        model = COMMAND_ARGUMENTS[values['operation']]
        arguments = values.get('arguments')
        if isinstance(arguments, BaseModel) and type(arguments) is not model:
            raise ValueError('命令参数不属于该操作')
        values['arguments'] = model.model_validate(arguments)
        return values


class BrowserElement(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    ref: str = Field(pattern=r'^e[0-9]{1,4}$')
    tag: str = Field(max_length=100)
    role: str | None = Field(default=None, max_length=200)
    text: str = Field(max_length=200)


class BrowserSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    page_ref: str = Field(pattern=r'^page_[a-f0-9]{20}$')
    snapshot_revision: int = Field(ge=1)
    url: str = Field(max_length=8000)
    title: str = Field(max_length=2000)
    text: str = Field(max_length=2_000_000)
    collected_text: str | None = Field(default=None, max_length=2_000_000,
        description='本次固定 DOM 观察的完整受限正文，随 R 持久保存供续读')
    elements: list[BrowserElement] = Field(max_length=120)
    text_offset: int = Field(ge=0)
    text_limit: int = Field(ge=1, le=2_000_000)
    text_total_chars: int = Field(ge=0, le=2_000_000)
    text_next_offset: int | None = None
    text_truncated: bool
    collection_truncated: bool
    collection_limit_chars: int = Field(ge=1, le=2_000_000)
    coverage: Literal['browser_dom_text'] = 'browser_dom_text'
    pixels_loaded: Literal[False] = False


class BrowserScreenshot(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    page_ref: str = Field(pattern=r'^page_[a-f0-9]{20}$')
    artifact_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    size_bytes: int = Field(ge=1, le=4 * 1024 * 1024)
    area: Literal['viewport', 'full_page']


class BrowserCommandRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    execution_id: str
    request: BrowserCommand
    status: Literal['accepted', 'running', 'completed', 'failed', 'unknown']
    accepted_at: float
    updated_at: float
    result: BrowserSnapshot | BrowserScreenshot | None = None
    error: str | None = Field(default=None, max_length=2000)


class BrowserWorkerReply(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    command_id: str = Field(pattern=r'^cmd_[a-f0-9]{32}$')
    status: Literal['completed', 'failed', 'unknown']
    snapshot: BrowserSnapshot | None = None
    png_base64: str | None = Field(default=None, max_length=5_592_408)
    error: str | None = Field(default=None, max_length=2000)
