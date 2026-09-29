"""Native directory overview format and refresh receipts at the memory boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class OverviewFreshness(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    total_entries: int = Field(ge=0)
    sampled_entries: int = Field(ge=0)
    unsampled_entries: int = Field(ge=0)
    pending_child_changes: int = Field(ge=0)
    missing_summary_entries: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def consistent_counts(self):
        if self.sampled_entries + self.unsampled_entries != self.total_entries:
            raise ValueError("sampled + unsampled must equal total_entries")
        return self


class OverviewMetadata(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    directory: str
    freshness: OverviewFreshness | None = None


@dataclass(frozen=True, slots=True)
class NativeOverview:
    path: str
    content: str
    freshness: OverviewFreshness | None

    def as_dict(self) -> dict:
        return {"path": self.path, "content": self.content,
                "freshness": None if self.freshness is None else self.freshness.model_dump(),
                "coverage": "direct_children"}


def parse_overview(text: str, *, uri: str, path: str) -> NativeOverview:
    """Require native metadata; never infer freshness from text or a placeholder."""
    try:
        lines = text.splitlines(keepends=True)
        if not lines or lines[0].rstrip("\r\n") != "---":
            raise ValueError("overview must contain native YAML frontmatter")
        closing = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
        if closing is None:
            raise ValueError("overview frontmatter is not closed")
        metadata = OverviewMetadata.model_validate(yaml.safe_load("".join(lines[1:closing])))
        if metadata.directory.rstrip("/") != uri.rstrip("/"):
            raise ValueError("overview directory differs from requested memory directory")
        return NativeOverview(path, "".join(lines[closing + 1:]).lstrip("\r\n"), metadata.freshness)
    except (ValueError, yaml.YAMLError) as error:
        raise ValueError(f"OpenViking invalid overview: {error}; raw={text[:1000]!r}") from error


class OverviewRefresh(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore")
    status: Literal["completed"]
    uri: str
    object_type: Literal["memory"]
    mode: Literal["semantic_and_vectors"]
    scanned_records: int = Field(ge=0)
    rebuilt_records: int = Field(ge=0)
    deleted_records: int = Field(ge=0)
    unsupported_records: int = Field(ge=0)
    failed_records: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    warnings: list[str]


def parse_refresh(value: object, *, uri: str, raw: str) -> OverviewRefresh:
    try:
        result = OverviewRefresh.model_validate(value)
        if result.uri != uri:
            raise ValueError("refresh returned another memory directory")
        return result
    except ValueError as error:
        raise ValueError(f"OpenViking invalid overview refresh: {error}; raw={raw[:1000]!r}") from error
