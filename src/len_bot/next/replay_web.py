"""Explicit frozen HTTP inputs for isolated development replay, never a network fallback."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

STRICT = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class SearchRecording(BaseModel):
    model_config = STRICT
    query: str = Field(min_length=1, max_length=500, pattern=r'\S')
    rss: str
    fetched_at: float = Field(gt=0, allow_inf_nan=False)
    status_code: int = Field(ge=100, le=599)


class DocumentRecording(BaseModel):
    model_config = STRICT
    url: str
    final_url: str
    content_type: str = Field(min_length=1)
    body: str
    fetched_at: float = Field(gt=0, allow_inf_nan=False)
    status_code: int = Field(ge=100, le=599)

    @field_validator('url', 'final_url')
    @classmethod
    def http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {'http', 'https'} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or any(c.isspace() or ord(c) < 32 for c in value)):
            raise ValueError('recorded URL must be an actual complete HTTP(S) URL without credentials')
        return value


class WebRecordings(BaseModel):
    model_config = STRICT
    source: str = Field(min_length=1, pattern=r'\S')
    searches: list[SearchRecording]
    documents: list[DocumentRecording]

    @model_validator(mode='after')
    def unique_inputs(self):
        if len({item.query for item in self.searches}) != len(self.searches):
            raise ValueError('each recorded search query must have exactly one response')
        if len({item.url for item in self.documents}) != len(self.documents):
            raise ValueError('each recorded document URL must have exactly one response')
        return self


class RecordedWeb:
    def __init__(self, manifest: Path):
        self.manifest = manifest
        self.raw = manifest.read_bytes()
        try:
            self.data = WebRecordings.model_validate_json(self.raw)
        except ValidationError as error:
            raise ValueError(f'{manifest}: invalid web recordings: {error}; raw={self.raw[:1000]!r}') from error
        self.searches = {item.query: item for item in self.data.searches}
        self.documents = {item.url: item for item in self.data.documents}
        self.payloads: dict[str, bytes] = {}
        for name in [*(item.rss for item in self.data.searches), *(item.body for item in self.data.documents)]:
            relative = Path(name)
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:
                raise ValueError(f'recorded response must be a file below its manifest: {name!r}')
            if relative.as_posix() != name or name.casefold() == 'manifest.json':
                raise ValueError(f'recorded response path must be canonical and cannot be manifest.json: {name!r}')
            path = manifest.parent / relative
            if not path.resolve().is_relative_to(manifest.parent.resolve()) or not path.is_file():
                raise ValueError(f'recorded response is missing or outside its manifest: {path}')
            if path.stat().st_size > 10_000_000:
                raise ValueError(f'recorded response exceeds maximum document size: {path}')
            self.payloads[name] = path.read_bytes()

    def search(self, query: str) -> tuple[SearchRecording, bytes]:
        if query not in self.searches:
            raise ValueError(f'No frozen RSS response for exact query {query!r}; network access is not used')
        item = self.searches[query]
        return item, self.payloads[item.rss]

    def document(self, url: str) -> tuple[DocumentRecording, bytes]:
        if url not in self.documents:
            raise ValueError(f'No frozen document for exact URL {url!r}; network access is not used')
        item = self.documents[url]
        return item, self.payloads[item.body]

    def freeze(self, destination: Path) -> Path:
        if 'manifest.json' in self.payloads:
            raise ValueError('manifest.json is reserved for the frozen source manifest')
        destination.mkdir()
        for name, data in self.payloads.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        target = destination / 'manifest.json'
        target.write_bytes(self.raw)
        return target
