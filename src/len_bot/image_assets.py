"""Verified original images and the resized copies sent as stickers."""
from dataclasses import dataclass, field
from io import BytesIO
from PIL import Image, ImageSequence

MAX_IMAGE_BYTES = 10_000_000
MAX_IMAGE_PIXELS = 25_000_000
MIME_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif", "WEBP": "image/webp"}
STICKER_MAX_DIMENSION = 320


def sticker_bytes(data: bytes) -> bytes:
    """Fit a sent sticker copy to one canvas limit, preserving animation and originals."""
    with Image.open(BytesIO(data)) as image:
        if max(image.size) <= STICKER_MAX_DIMENSION:
            return data
        preview = image.copy()
        preview.thumbnail((STICKER_MAX_DIMENSION, STICKER_MAX_DIMENSION), Image.Resampling.LANCZOS)
        size, format = preview.size, image.format
        frames, durations = [], []
        for frame in ImageSequence.Iterator(image):
            mode = 'RGB' if format == 'JPEG' else 'RGBA'
            frames.append(frame.convert(mode).resize(size, Image.Resampling.LANCZOS))
            durations.append(frame.info.get('duration', 0))
        options = {}
        if len(frames) > 1:
            options.update(save_all=True, append_images=frames[1:], duration=durations)
            if 'loop' in image.info:
                options['loop'] = image.info['loop']
            if format == 'GIF':
                options['disposal'] = 2
        output = BytesIO()
        frames[0].save(output, format=format, **options)
        return output.getvalue()

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
