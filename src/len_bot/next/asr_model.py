"""A single explicit audio/transcriptions request; no chat fallback or automatic retry."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class ASRBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    api: Literal["openai-audio"] = "openai-audio"
    provider: str
    model: str
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")

    @field_validator("provider", "model")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class AudioSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    max_bytes: int = Field(default=16 * 1024 * 1024, gt=0, le=25 * 1024 * 1024)
    max_seconds: float = Field(default=300, gt=0, le=3600, allow_inf_nan=False)
    timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)


class UsageDetails(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    audio_tokens: int | None = Field(default=None, ge=0)
    text_tokens: int | None = Field(default=None, ge=0)


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    type: Literal["tokens"]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    input_token_details: UsageDetails | None = None


class DurationUsage(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)
    type: Literal["duration"]
    seconds: float = Field(ge=0, allow_inf_nan=False)


USAGE = TypeAdapter(Annotated[TokenUsage | DurationUsage, Field(discriminator="type")])


class ASRProtocolError(ValueError):
    def __init__(self, message: str, response: object):
        super().__init__(message)
        self.response = response


@dataclass(frozen=True)
class Transcript:
    text: str
    usage: dict | None
    response: dict


def parse_transcription(body: object) -> Transcript:
    try:
        if not isinstance(body, dict) or not isinstance(body.get("text"), str):
            raise ValueError("audio/transcriptions response must have string text")
        usage = body.get("usage")
        if usage is not None:
            USAGE.validate_python(usage)
        return Transcript(body["text"], usage, body)
    except ValueError as error:
        raise ASRProtocolError(f"Invalid ASR response: {error}; raw={repr(body)[:500]}", body) from error


async def transcribe_audio(binding: ASRBinding, *, base_url: str, api_key: str, wav: bytes) -> Transcript:
    data = {"model": binding.model, "response_format": "json"}
    if binding.language is not None:
        data["language"] = binding.language
    async with asyncio.timeout(binding.timeout_seconds), httpx.AsyncClient(
            timeout=binding.timeout_seconds, trust_env=False, follow_redirects=False) as client:
        response = await client.post(base_url.rstrip("/") + "/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"}, data=data,
            files={"file": ("record.wav", wav, "audio/wav")})
        if not response.is_success:
            raise RuntimeError(f"ASR HTTP {response.status_code}: {response.text[:2000]}")
        try:
            body = response.json()
        except ValueError as error:
            raise ASRProtocolError(f"ASR response is not JSON: {error}; raw={response.text[:500]!r}",
                                   response.text) from error
        return parse_transcription(body)
