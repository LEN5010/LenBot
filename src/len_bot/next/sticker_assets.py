"""Original image validation and an immutable selected group sticker."""

from dataclasses import dataclass, field
from io import BytesIO

from PIL import Image


MAX_STICKER_BYTES = 10_000_000
MAX_STICKER_PIXELS = 25_000_000
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}


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


def inspect_sticker(data: bytes) -> tuple[str, int, int, bool]:
    """Validate the actual original without decoding every animated frame."""
    try:
        if not data or len(data) > MAX_STICKER_BYTES:
            raise ValueError(f"sticker asset must contain 1..{MAX_STICKER_BYTES} bytes")
        with Image.open(BytesIO(data)) as image:
            if image.format not in MIME_TYPES:
                raise ValueError(f"unsupported sticker image format: {image.format!r}")
            width, height = image.size
            if width * height > MAX_STICKER_PIXELS:
                raise ValueError(f"sticker canvas exceeds {MAX_STICKER_PIXELS} pixels")
            mime_type = MIME_TYPES[image.format]
            animated = getattr(image, "n_frames", 1) > 1
            image.verify()
        return mime_type, width, height, animated
    except (OSError, ValueError, EOFError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError(f"invalid sticker asset: {error}; original prefix={data[:32]!r}") from error
