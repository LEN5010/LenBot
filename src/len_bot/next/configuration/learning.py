"""Per-scene expression, jargon and sticker learning settings."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from .types import STRICT
from ..memory.embeddings import EmbeddingBinding


class LearningSettings(BaseModel):
    model_config = STRICT

    extract: bool = True
    jargon_extract: bool = False
    collect_stickers: bool = False
    reply_effects: bool = False
    min_messages: int = Field(default=20, ge=1, le=100, strict=True)
    batch_size: int = Field(default=50, ge=1, le=100, strict=True)
    idle_seconds: float = Field(default=300.0, gt=0, allow_inf_nan=False)
    max_age_seconds: float = Field(default=1800.0, gt=0, allow_inf_nan=False)
    auto_adopt: bool = False
    embedding: EmbeddingBinding | None = None

    @model_validator(mode="after")
    def valid_batch_window(self) -> LearningSettings:
        if self.batch_size < self.min_messages:
            raise ValueError("learning.batch_size must be at least learning.min_messages")
        if self.max_age_seconds < self.idle_seconds:
            raise ValueError("learning.max_age_seconds must be at least learning.idle_seconds")
        return self
