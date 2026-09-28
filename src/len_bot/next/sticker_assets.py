"""An immutable selected group sticker and its actual source message."""
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class CollectedSticker:
    candidate_id: int
    media_id: int
    source_message_seq: int
    source_image_index: int
    description: str
    emotions: tuple[str, ...]
    tags: tuple[str, ...]
    data: bytes = field(repr=False)
