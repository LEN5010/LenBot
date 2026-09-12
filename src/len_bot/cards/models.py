"""Small metadata models for deterministic card artifacts."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CardSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    url: str = ""
    result_ids: list[str] = Field(default_factory=list)
    fetched_at: float | None = None


class CardRenderMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    theme_version: str = "light-v1"
    source_result_ids: list[str] = Field(default_factory=list)
    rendered_at: float
    page_index: int = Field(default=1, ge=1)
    page_count: int = Field(default=1, ge=1)
