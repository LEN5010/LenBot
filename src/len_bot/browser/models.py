from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BrowserOpenInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1, max_length=4000)


class BrowserPageInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    page_ref: str = Field(pattern=r'^page_[a-f0-9]{20}$')
    text_offset: int = Field(default=0, ge=0)
    text_limit: int | None = Field(default=None, ge=100, le=100_000)
    refresh: bool = Field(default=False, description='true 时重新抓 DOM 并形成新 snapshot；续读必须为 false')


class BrowserInteractInput(BrowserPageInput):
    snapshot_revision: int = Field(ge=1)
    element_ref: str | None = Field(default=None, pattern=r'^e[0-9]{1,4}$')
    action: Literal['click', 'scroll']
    value: str | None = Field(default=None, max_length=100)


class BrowserCaptureInput(BrowserPageInput):
    snapshot_revision: int | None = Field(default=None, ge=1)
    area: Literal['viewport', 'full_page'] = 'viewport'
