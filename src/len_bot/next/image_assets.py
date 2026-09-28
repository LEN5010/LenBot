"""Actual original image bytes and their verified format; no content-based identity."""
from dataclasses import dataclass, field
from io import BytesIO
from PIL import Image

MAX_IMAGE_BYTES = 10_000_000
MAX_IMAGE_PIXELS = 25_000_000
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}

@dataclass(frozen=True, slots=True)
class OriginalImage:
    data: bytes = field(repr=False)
    mime_type: str
    width: int
    height: int
    animated: bool


def inspect_image(data: bytes) -> tuple[str, int, int, bool]:
    """Validate the actual original without decoding every animated frame."""
    try:
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"image asset must contain 1..{MAX_IMAGE_BYTES} bytes")
        with Image.open(BytesIO(data)) as image:
            if image.format not in MIME_TYPES:
                raise ValueError(f"unsupported image format: {image.format!r}")
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError(f"image canvas exceeds {MAX_IMAGE_PIXELS} pixels")
            mime_type = MIME_TYPES[image.format]
            animated = getattr(image, "n_frames", 1) > 1
            image.verify()
        return mime_type, width, height, animated
    except (OSError, ValueError, EOFError, SyntaxError, Image.DecompressionBombError) as error:
        raise ValueError(f"invalid image asset: {error}; original prefix={data[:32]!r}") from error
