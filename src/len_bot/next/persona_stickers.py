"""Load one role's indexed sticker files as an immutable runtime byte snapshot."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import stat

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
import yaml

from .image_assets import MAX_IMAGE_BYTES, inspect_image




@dataclass(frozen=True, slots=True)
class PersonaSticker:
    file: str
    description: str
    emotions: tuple[str, ...]
    tags: tuple[str, ...]
    mime_type: str
    width: int
    height: int
    animated: bool
    data: bytes = field(repr=False)


class StickerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    file: str
    description: str
    emotions: list[str]
    tags: list[str]

    @field_validator("file")
    @classmethod
    def relative_file(cls, value: str) -> str:
        if (not value or value.startswith("/") or "\\" in value
                or any(part in {"", ".", ".."} for part in value.split("/"))):
            raise ValueError("file must be a normal relative POSIX path below stickers/")
        return value

    @field_validator("description")
    @classmethod
    def nonblank_description(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("description must not be blank")
        return value

    @field_validator("emotions", "tags")
    @classmethod
    def nonblank_labels(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("labels must not contain blank text")
        return values


def _image(path: Path, entry: StickerEntry) -> PersonaSticker:
    try:
        if not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode):
            raise ValueError("sticker asset must be a regular file")
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
        mime_type, width, height, animated = inspect_image(data)
    except (OSError, ValueError) as error:
        raise ValueError(f"{path}: invalid sticker asset: {error}") from error
    return PersonaSticker(entry.file, entry.description, tuple(entry.emotions),
                          tuple(entry.tags), mime_type, width, height, animated, data)


def load_stickers(persona_directory: Path) -> dict[str, PersonaSticker]:
    """Read exactly the indexed images; absent stickers/ means no role stickers."""
    directory = persona_directory / "stickers"
    if not directory.exists() and not directory.is_symlink():
        return {}
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError(f"{directory}: stickers must be an actual directory")
    index = directory / "index.yaml"
    try:
        if not stat.S_ISREG(index.stat(follow_symlinks=False).st_mode):
            raise ValueError(f"{index}: index.yaml must be a regular file")
        raw = index.read_bytes()
    except OSError as error:
        raise ValueError(f"{index}: cannot read sticker index: {error}") from error
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        fragment = raw[max(0, error.start - 30):error.end + 30]
        raise ValueError(f"{index}: invalid UTF-8: {error}; raw={fragment!r}") from error
    return parse_sticker_index(persona_directory, content)


def sticker_entries(index: Path, content: str) -> list[StickerEntry]:
    try:
        items = yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ValueError(f"{index}: invalid YAML: {error}; raw={content[:500]!r}") from error
    if not isinstance(items, list):
        raise ValueError(f"{index}: expected a list; raw={content[:500]!r}")

    result: list[StickerEntry] = []
    names: set[str] = set()
    for position, item in enumerate(items):
        try:
            entry = StickerEntry.model_validate(item)
        except ValidationError as error:
            details = "; ".join(f"{'.'.join(map(str, issue['loc']))}: {issue['msg']}"
                                for issue in error.errors(include_input=False))
            raise ValueError(f"{index}[{position}]: {details}; raw={str(item)[:500]!r}") from error
        if entry.file in names:
            raise ValueError(f"{index}[{position}]: duplicate sticker file {entry.file!r}; raw={str(item)[:500]!r}")
        names.add(entry.file)
        result.append(entry)
    return result


def parse_sticker_index(persona_directory: Path, content: str) -> dict[str, PersonaSticker]:
    """Validate one operator-edited index against its actual images, without writing files."""
    directory = persona_directory / 'stickers'
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise ValueError(f'{directory}: stickers must be an actual directory')
    index = directory / 'index.yaml'
    result: dict[str, PersonaSticker] = {}
    for position, entry in enumerate(sticker_entries(index, content)):
        parts = entry.file.split("/")
        path = directory.joinpath(*parts)
        for component in (directory.joinpath(*parts[:end]) for end in range(1, len(parts) + 1)):
            if component.is_symlink():
                raise ValueError(f"{index}[{position}]: sticker path traverses symbolic link: {component}; "
                                 f"raw={entry.model_dump()!r}")
        try:
            result[entry.file] = _image(path, entry)
        except ValueError as error:
            raise ValueError(f"{index}[{position}]: {error}; raw={entry.model_dump()!r}") from error
    return result
