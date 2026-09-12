from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class BrowserOpenInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    url: str = Field(min_length=1, max_length=4000)


class BrowserPageInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    page_ref: str = Field(pattern=r'^page_[a-f0-9]{20}$')


class BrowserInteractInput(BrowserPageInput):
    snapshot_revision: int = Field(ge=1)
    element_ref: str | None = Field(default=None, pattern=r'^e[0-9]{1,4}$')
    action: Literal['click', 'scroll']
    value: str | None = Field(default=None, max_length=100)


class BrowserCaptureInput(BrowserPageInput):
    snapshot_revision: int | None = Field(default=None, ge=1)
    area: Literal['viewport', 'full_page'] = 'viewport'
