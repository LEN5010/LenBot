"""One configured OpenAI-compatible embeddings HTTP boundary for text."""

from __future__ import annotations

import asyncio
import json
import math
import struct
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models.tokens import TokenUsage


class EmbeddingBinding(BaseModel):
    """Reference to an existing model provider, without another key."""

    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    provider: str
    model: str
    dimensions: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("provider", "model")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class EmbeddingSettings(EmbeddingBinding):
    """Resolved by the host from models.providers at one configuration boundary."""

    base_url: str
    api_key: str = Field(repr=False)
    proxy: str | None = Field(default=None, repr=False)
    timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("embedding base_url must be HTTP(S) without credentials/query/fragment")
        return value.rstrip("/")

    @field_validator("api_key")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if not value.strip() or "\r" in value or "\n" in value:
            raise ValueError("embedding api_key must be nonblank without line breaks")
        return value


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    dimensions: int
    usage: dict | None
    token_usage: TokenUsage | None


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _token_usage(usage: dict | None) -> TokenUsage | None:
    if usage is None:
        return None
    prompt = usage.get("prompt_tokens")
    if prompt is not None and (type(prompt) is not int or prompt < 0):
        raise ValueError("usage.prompt_tokens must be a nonnegative integer or null")
    details = usage.get("prompt_tokens_details")
    if details is not None and not isinstance(details, dict):
        raise ValueError("usage.prompt_tokens_details must be an object or null")
    cached = None if details is None else details.get("cached_tokens")
    if cached is not None and (type(cached) is not int or cached < 0):
        raise ValueError("usage.prompt_tokens_details.cached_tokens must be a nonnegative integer or null")
    if prompt is not None and cached is not None and cached > prompt:
        raise ValueError("usage.prompt_tokens_details.cached_tokens exceeds usage.prompt_tokens")
    return TokenUsage(prompt, 0, cached)


def parse_embeddings(body: object, count: int, expected_dimensions: int | None) -> EmbeddingBatch:
    """Parse the provider result once; never infer a missing row or dimension."""
    try:
        if not isinstance(body, dict):
            raise ValueError("response must be an object")
        data = body["data"]
        if not isinstance(data, list) or len(data) != count:
            raise ValueError(f"data must contain exactly {count} rows")
        ordered: list[tuple[float, ...] | None] = [None] * count
        dimensions: int | None = None
        for row in data:
            if not isinstance(row, dict):
                raise ValueError("embedding row must be an object")
            index = row["index"]
            if type(index) is not int or not 0 <= index < count or ordered[index] is not None:
                raise ValueError(f"embedding row has invalid or duplicate index: {index!r}")
            raw_vector = row["embedding"]
            if not isinstance(raw_vector, list) or not raw_vector:
                raise ValueError(f"embedding[{index}] must be a nonempty number array")
            if any(type(value) not in {int, float} or not math.isfinite(value) for value in raw_vector):
                raise ValueError(f"embedding[{index}] contains a non-finite or nonnumeric value")
            vector = tuple(float(value) for value in raw_vector)
            try:
                packed = struct.pack(f"<{len(vector)}f", *vector)
            except (OverflowError, struct.error) as error:
                raise ValueError(f"embedding[{index}] is outside float32 range") from error
            if all(value == 0 for value in struct.unpack(f"<{len(vector)}f", packed)):
                raise ValueError(f"embedding[{index}] becomes a zero float32 vector and cannot use cosine distance")
            if dimensions is None:
                dimensions = len(vector)
            elif len(vector) != dimensions:
                raise ValueError("embedding rows have different dimensions")
            ordered[index] = vector
        if any(vector is None for vector in ordered):
            raise ValueError("embedding indexes do not cover every input")
        if expected_dimensions is not None and dimensions != expected_dimensions:
            raise ValueError(f"embedding dimensions {dimensions} differ from configured {expected_dimensions}")
        usage = body.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("usage must be an object or null")
        return EmbeddingBatch(vectors=tuple(ordered), dimensions=dimensions, usage=usage,
                              token_usage=_token_usage(usage))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        fragment = json.dumps(body, ensure_ascii=False, default=repr)[:500]
        raise ValueError(f"invalid embedding response: {error}; response fragment: {fragment}") from error


class EmbeddingClient:
    def __init__(self, settings: EmbeddingSettings):
        self.settings = settings
        self._client = httpx.AsyncClient(
            timeout=settings.timeout_seconds, trust_env=False, follow_redirects=False,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False, proxy=settings.proxy),
            headers={"Authorization": f"Bearer {settings.api_key}"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> EmbeddingClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        if not texts or any(not text.strip() for text in texts):
            raise ValueError("embedding input must contain nonblank text")
        deadline = asyncio.timeout(self.settings.timeout_seconds)
        try:
            async with deadline:
                response = await self._client.post(
                    f"{self.settings.base_url}/embeddings",
                    json={"model": self.settings.model, "input": texts, "encoding_format": "float",
                          **({"dimensions": self.settings.dimensions} if self.settings.dimensions is not None else {})},
                )
        except TimeoutError as error:
            if deadline.expired():
                raise TimeoutError(f"embedding request exceeded {self.settings.timeout_seconds} seconds") from error
            raise
        raw = response.text
        if response.status_code != 200:
            raise ValueError(f"embedding HTTP {response.status_code}: {raw[:2000]}")
        try:
            body = json.loads(raw, parse_constant=_reject_constant)
        except ValueError as error:
            raise ValueError(f"embedding response invalid JSON: {error}; response fragment: {raw[:500]!r}") from error
        return parse_embeddings(body, len(texts), self.settings.dimensions)
