from typing import Any, Literal, TypedDict
from pydantic import BaseModel, ConfigDict, Field, model_validator

CHARACTER_REFERENCE_TAG = '人物参考'


def media_purpose(tags):
    if CHARACTER_REFERENCE_TAG in tags:
        return 'character_reference'
    return 'sticker' if '表情包' in tags else 'media'


class CuratedMediaBaseline(BaseModel):
    """An operator's original public asset values, without paths or image bytes."""
    model_config = ConfigDict(extra='forbid', frozen=True)
    id: str
    scope: str
    source_event_id: str
    created_at: float = Field(allow_inf_nan=False)
    curated: Literal[True]
    description: str
    tags: list[str]
    enabled: bool
    palette_order: int | None


class MediaEditConflict(ValueError):
    def __init__(self, field: str):
        self.field = field
        super().__init__('素材保存值已变化：' + field + '；本次修改未保存，请核对当前值')


class CuratedMediaSavedError(RuntimeError):
    def __init__(self, asset_id: str, scope: str, event_id: str, phase: str, reason: str):
        self.asset_id, self.scope, self.event_id, self.phase = asset_id, scope, event_id, phase
        super().__init__('素材已入库，但后续' + ('事件登记' if phase == 'event' else '保存值读取') + '失败：' + reason)


class PreparedMediaContext(TypedDict):
    """Transient pixels and their provenance; only the manifest belongs in traces."""

    blocks: list[dict[str, Any]]
    manifest: list[dict[str, Any]]


class MessageSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["text", "image", "video", "audio", "at"]
    text: str | None = None
    asset_id: str | None = None
    qq_uid: str | None = Field(default=None, pattern=r"^[1-9][0-9]*$")

    @model_validator(mode="after")
    def validate_segment(self):
        if self.type == "text" and (not self.text or self.asset_id is not None or self.qq_uid is not None):
            raise ValueError("Text segments need text, not an asset_id")
        if self.type in {"image", "video", "audio"} and (not self.asset_id or self.text is not None or self.qq_uid is not None):
            raise ValueError("Media segments need an asset_id, not raw text/URLs")
        if self.type == "at" and (not self.qq_uid or self.text is not None or self.asset_id is not None):
            raise ValueError("Member mentions need a numeric qq_uid only")
        return self


def segment_text(segments: list[MessageSegment]) -> str:
    return "".join(segment.text if segment.type == "text" else
                   f"[CQ:at,qq={segment.qq_uid}]" if segment.type == "at" else f"[{segment.type}]" for segment in segments)
