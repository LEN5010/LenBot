"""Frozen native memory HTTP exchanges for isolated replay, with no network client."""

from __future__ import annotations

from collections import defaultdict, deque
import json
import os
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

if TYPE_CHECKING:
    from .memory_openviking import OpenVikingSettings

STRICT = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True, allow_inf_nan=False)


class MemoryRequest(BaseModel):
    model_config = STRICT
    user_id: str = Field(min_length=1)
    method: Literal['GET', 'POST', 'DELETE']
    endpoint: str
    params: dict[str, str | int | float | bool] | None
    body: dict[str, JsonValue] | None
    @field_validator('endpoint')
    @classmethod
    def native_endpoint(cls, value: str) -> str:
        if (value != '/health' and not value.startswith('/api/v1/')) or any(
                c.isspace() or ord(c) < 32 or c in '?#\\' for c in value):
            raise ValueError('endpoint must be /health or an actual /api/v1/ path without query')
        if any(part in {'.', '..'} for part in value.split('/')):
            raise ValueError('endpoint must not contain relative path segments')
        return value


class MemoryExchange(MemoryRequest):
    status_code: int = Field(ge=100, le=599)
    headers: dict[str, str]
    response: str
    received_at: float = Field(gt=0, allow_inf_nan=False)


class ConsumedExchange(MemoryRequest):
    exchange: int = Field(ge=0)
    received_at: float = Field(gt=0, allow_inf_nan=False)


class MemoryConsumption(BaseModel):
    model_config = STRICT
    consumed: list[ConsumedExchange]
    missing: list[MemoryRequest]
    unconsumed_exchanges: list[int]
    network_used: Literal[False]


class MemoryRecordings(BaseModel):
    model_config = STRICT
    source: str = Field(min_length=1, pattern=r'\S')
    base_url: str
    account_id: str = Field(min_length=1)
    scenes: dict[str, str] = Field(min_length=1)
    exchanges: list[MemoryExchange] = Field(min_length=1)

    @field_validator('base_url')
    @classmethod
    def http_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {'http', 'https'} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or any(c.isspace() for c in value)):
            raise ValueError('base_url must be the actual HTTP(S) service address without credentials')
        return value


def request_key(user: str, method: str, endpoint: str, params: dict | None, body: dict | None) -> str:
    # HTTP query values are strings on the wire, regardless of the caller's scalar type.
    query = sorted(httpx.QueryParams(params).multi_items()) if params is not None else []
    return json.dumps([user, method, endpoint, query, body], ensure_ascii=False,
                      allow_nan=False, sort_keys=True, separators=(',', ':'))


class RecordedMemory:
    def __init__(self, manifest: Path):
        self.manifest = manifest
        self.raw = manifest.read_bytes()
        try:
            self.data = MemoryRecordings.model_validate_json(self.raw)
        except ValidationError as error:
            raise ValueError(f'{manifest}: invalid native memory recordings: {error}; '
                             f'raw={self.raw[:1000]!r}') from error
        users = set(self.data.scenes.values())
        self.payloads: dict[str, bytes] = {}
        self.responses: dict[str, deque[int]] = defaultdict(deque)
        for position, item in enumerate(self.data.exchanges):
            if item.user_id not in users:
                raise ValueError(f'{manifest}: exchange {position} user is not a recorded scene identity')
            self.responses[request_key(item.user_id, item.method, item.endpoint,
                                       item.params, item.body)].append(position)
            name = item.response
            relative = Path(name)
            if (not relative.parts or relative.is_absolute() or '..' in relative.parts
                    or relative.as_posix() != name
                    or name.split('/')[0].casefold() in {'manifest.json', 'consumption.json'}):
                raise ValueError(f'recorded memory response must be a canonical payload path: {name!r}')
            path = manifest.parent / relative
            if not path.resolve().is_relative_to(manifest.parent.resolve()) or not path.is_file():
                raise ValueError(f'recorded memory response is missing or outside its manifest: {path}')
            if path.stat().st_size > 10_000_000:
                raise ValueError(f'recorded memory response exceeds 10 MB: {path}')
            self.payloads[name] = path.read_bytes()
        self.consumed: list[dict] = []
        self.missing: list[dict] = []

    def check_settings(self, settings: OpenVikingSettings) -> None:
        if (settings.base_url.rstrip('/') != self.data.base_url.rstrip('/')
                or settings.account_id != self.data.account_id
                or {scene: identity.user_id for scene, identity in settings.scenes.items()} != self.data.scenes):
            raise ValueError('Frozen memory service/account/scene identities differ from configured backend')

    def freeze(self, destination: Path) -> Path:
        destination.mkdir()
        for name, payload in self.payloads.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        manifest = destination / 'manifest.json'
        manifest.write_bytes(self.raw)
        return manifest


class RecordedMemoryClient:
    """Return recorded wire responses to the existing native boundary parsers."""
    def __init__(self, recordings: RecordedMemory, settings: OpenVikingSettings):
        recordings.check_settings(settings)
        self.recordings = recordings
        self.base_url = settings.base_url.rstrip('/')
        self.users = {identity.api_key: identity.user_id for identity in settings.scenes.values()}
        if len(self.users) != len(settings.scenes):
            raise ValueError('Memory replay requires a distinct configured key for each scene identity')
        report_path = recordings.manifest.parent / 'consumption.json'
        if report_path.exists():
            raw = report_path.read_bytes()
            try:
                state = MemoryConsumption.model_validate_json(raw)
            except ValidationError as error:
                raise ValueError(f'{report_path}: invalid native response consumption: {error}; raw={raw[:1000]!r}') from error
            consumed: set[int] = set()
            for used in state.consumed:
                position = used.exchange
                if position in consumed or position >= len(recordings.data.exchanges):
                    raise ValueError(f'{report_path}: duplicate or nonexistent consumed exchange {position}')
                original = recordings.data.exchanges[position]
                if (used.received_at != original.received_at or
                        request_key(used.user_id, used.method, used.endpoint, used.params, used.body) !=
                        request_key(original.user_id, original.method, original.endpoint, original.params, original.body)):
                    raise ValueError(f'{report_path}: consumed exchange {position} differs from its frozen request')
                consumed.add(position)
            remaining = sorted(set(range(len(recordings.data.exchanges))) - consumed)
            if remaining != state.unconsumed_exchanges:
                raise ValueError(f'{report_path}: remaining exchanges differ from actual consumption')
            for key, positions in recordings.responses.items():
                consumed_prefix = True
                for position in positions:
                    if position not in consumed:
                        consumed_prefix = False
                    elif not consumed_prefix:
                        raise ValueError(f'{report_path}: responses were not consumed in recorded request order')
                recordings.responses[key] = deque(position for position in positions if position not in consumed)
            recordings.consumed = [used.model_dump() for used in state.consumed]
            recordings.missing = [request.model_dump() for request in state.missing]

    async def get(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        return await self.request('GET', url, headers=headers)

    async def request(self, method: str, url: str, *, headers: dict[str, str],
                      params: dict | None = None, json: dict | None = None) -> httpx.Response:
        prefix = self.base_url + '/'
        if not url.startswith(prefix):
            raise ValueError(f'Recorded memory request is outside configured service: {url!r}')
        endpoint = url[len(self.base_url):]
        user = self.users[headers['X-API-Key']]
        key = request_key(user, method, endpoint, params, json)
        request = {'user_id': user, 'method': method, 'endpoint': endpoint, 'params': params, 'body': json}
        positions = self.recordings.responses.get(key)
        if not positions:
            self.recordings.missing.append(request)
            self._save_consumption()
            raise ValueError(f'No remaining frozen native memory response for {request!r}; network is not used')
        position = positions.popleft()
        item = self.recordings.data.exchanges[position]
        self.recordings.consumed.append({'exchange': position, 'received_at': item.received_at, **request})
        self._save_consumption()
        return httpx.Response(item.status_code, headers=item.headers,
                              content=self.recordings.payloads[item.response],
                              request=httpx.Request(method, url, params=params, json=json),
                              extensions={'recorded_at': item.received_at})

    async def aclose(self) -> None:
        self._save_consumption()

    def _save_consumption(self) -> None:
        remaining = sorted(position for positions in self.recordings.responses.values() for position in positions)
        report = {'consumed': self.recordings.consumed, 'missing': self.recordings.missing,
                  'unconsumed_exchanges': remaining, 'network_used': False}
        destination = self.recordings.manifest.parent / 'consumption.json'
        descriptor, temporary = tempfile.mkstemp(prefix='.consumption-', dir=destination.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                output.write(json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + '\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
