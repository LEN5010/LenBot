from typing import Literal
from pydantic import BaseModel, ConfigDict, model_validator


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


def segment_text(segments):
    return "".join(segment.text if segment.type == "text" else "[图片]" for segment in segments)


def normalize_message_body(message):
    if message.segments:
        message.content = segment_text(message.segments)
    if not message.content.strip():
        raise ValueError("A message needs nonempty content or image segments")
    return message
