from typing import Any, Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PreparedMediaContext(TypedDict):
    """Transient pixels and their provenance; only the manifest belongs in traces."""

    blocks: list[dict[str, Any]]
    manifest: list[dict[str, Any]]


class MessageSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "image", "at"]
    text: str | None = None
    asset_id: str | None = None
    qq_uid: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")

    @model_validator(mode="after")
    def validate_segment(self):
        if self.type == "text" and (not self.text or self.asset_id is not None or self.qq_uid is not None):
            raise ValueError("Text segments need text, not an asset_id")
        if self.type == "image" and (not self.asset_id or self.text is not None or self.qq_uid is not None):
            raise ValueError("Image segments need an asset_id, not raw text/URLs")
        if self.type == "at" and (not self.qq_uid or self.text is not None or self.asset_id is not None):
            raise ValueError("Member mentions need a numeric qq_uid only")
        return self


def segment_text(segments: list[MessageSegment]) -> str:
    return "".join(segment.text if segment.type == "text" else
                   f"[CQ:at,qq={segment.qq_uid}]" if segment.type == "at" else "[图片]" for segment in segments)
