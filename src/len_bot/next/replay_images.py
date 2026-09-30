"""Frozen original image bytes indexed only by real platform message and segment position."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

STRICT = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class ImageRecording(BaseModel):
    model_config = STRICT
    message: str = Field(min_length=1, pattern=r'\S')
    image: int = Field(gt=0)
    file: str
    fetched_at: float = Field(gt=0, allow_inf_nan=False)


class ImageRecordings(BaseModel):
    model_config = STRICT
    source: str = Field(min_length=1, pattern=r'\S')
    images: list[ImageRecording]

    @model_validator(mode='after')
    def unique_positions(self):
        if len({(item.message, item.image) for item in self.images}) != len(self.images):
            raise ValueError('each platform message image position must have exactly one original')
        return self


class RecordedImages:
    def __init__(self, manifest: Path, *, max_bytes: int):
        self.raw = manifest.read_bytes()
        try:
            self.data = ImageRecordings.model_validate_json(self.raw)
        except ValidationError as error:
            raise ValueError(f'{manifest}: invalid image recordings: {error}; raw={self.raw[:1000]!r}') from error
        self.positions = {(item.message, item.image): item for item in self.data.images}
        self.payloads: dict[str, bytes] = {}
        for item in self.data.images:
            relative = Path(item.file)
            if (relative.is_absolute() or '..' in relative.parts or not relative.parts
                    or relative.as_posix() != item.file or item.file.casefold() == 'manifest.json'):
                raise ValueError(f'original image must be a canonical file below its manifest: {item.file!r}')
            path = manifest.parent / relative
            if not path.resolve().is_relative_to(manifest.parent.resolve()) or not path.is_file():
                raise ValueError(f'original image is missing or outside its manifest: {path}')
            if path.stat().st_size > max_bytes:
                raise ValueError(f'original image exceeds configured {max_bytes} byte limit: {path}')
            self.payloads[item.file] = path.read_bytes()

    def image(self, message: str, position: int) -> tuple[bytes, float]:
        key = (message, position)
        if key not in self.positions:
            raise ValueError(f'No frozen original for platform message {message!r} image {position}; network is not used')
        item = self.positions[key]
        return self.payloads[item.file], item.fetched_at

    def freeze(self, destination: Path) -> Path:
        destination.mkdir()
        for name, data in self.payloads.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        target = destination / 'manifest.json'
        target.write_bytes(self.raw)
        return target
