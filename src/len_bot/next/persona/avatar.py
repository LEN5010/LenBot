"""Optional original PNG for panel display, never model input or chat material."""

from pathlib import Path
import stat

from ...image_assets import MAX_IMAGE_BYTES, OriginalImage, inspect_image


def avatar_file(directory: Path) -> Path:
    target = directory / 'avatar.png'
    if target.is_symlink():
        raise ValueError(f'Role avatar cannot be a file link: {target}')
    if target.exists() and not stat.S_ISREG(target.stat(follow_symlinks=False).st_mode):
        raise ValueError(f'Role avatar must be a regular file: {target}')
    return target


def parse_avatar(data: bytes) -> OriginalImage:
    metadata = inspect_image(data)
    if metadata[0] != 'image/png':
        raise ValueError(f'avatar.png must contain actual PNG bytes, not {metadata[0]}; original prefix={data[:32]!r}')
    return OriginalImage(data, *metadata)


def load_avatar(directory: Path) -> OriginalImage | None:
    target = avatar_file(directory)
    if not target.exists():
        return None
    with target.open('rb') as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    try:
        return parse_avatar(data)
    except ValueError as error:
        raise ValueError(f'{target}: {error}') from error
