from typing import Any, Literal, TypedDict
from pydantic import BaseModel, ConfigDict, model_validator


class PreparedMediaContext(TypedDict):
    """Transient pixels and their provenance; only the manifest belongs in traces."""

    blocks: list[dict[str, Any]]
    manifest: list[dict[str, Any]]


class MessageSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "image"]
    text: str | None = None
    asset_id: str | None = None

    @model_validator(mode="after")
    def validate_segment(self):
        if self.type == "text" and (not self.text or self.asset_id is not None):
            raise ValueError("Text segments need text, not an asset_id")
        if self.type == "image" and (not self.asset_id or self.text is not None):
            raise ValueError("Image segments need an asset_id, not raw text/URLs")
        return self


def segment_text(segments: list[MessageSegment]) -> str:
    return "".join(segment.text if segment.type == "text" else "[图片]" for segment in segments)
