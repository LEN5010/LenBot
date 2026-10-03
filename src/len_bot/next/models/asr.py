"""A single explicit audio/transcriptions request; no chat fallback or automatic retry."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


from .pricing import Rate


class DurationPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["duration"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    per_second: Rate


class AudioTokenPrice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    type: Literal["tokens"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    input_audio: Rate
    input_text: Rate
    output: Rate


AudioPrice = Annotated[DurationPrice | AudioTokenPrice, Field(discriminator="type")]


class ASRBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    api: Literal["openai-audio"] = "openai-audio"
    provider: str
    model: str
    timeout_seconds: float = Field(default=60, gt=0, allow_inf_nan=False)
    language: str | None = Field(default=None, pattern=r"^[a-z]{2}$")
    price: AudioPrice | None = None

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
    wait_seconds: float = Field(default=2, ge=0, le=30, allow_inf_nan=False)


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
    def __init__(self, message: str, response: object, *, usage: dict | None = None,
                 metering: TokenUsage | DurationUsage | None = None):
        super().__init__(message)
        self.response = response
        self.usage = usage
        self.metering = metering


@dataclass(frozen=True)
class Transcript:
    text: str
    usage: dict | None
    response: dict
    metering: TokenUsage | DurationUsage | None


def parse_transcription(body: object) -> Transcript:
    usage, metering = None, None
    try:
        if not isinstance(body, dict):
            raise ValueError("audio/transcriptions response must be an object")
        raw_usage = body.get("usage")
        if raw_usage is not None:
            metering = USAGE.validate_python(raw_usage)
            usage = raw_usage
        if not isinstance(body.get("text"), str):
            raise ValueError("audio/transcriptions response must have string text")
        return Transcript(body["text"], usage, body, metering)
    except ValueError as error:
        raise ASRProtocolError(f"Invalid ASR response: {error}; raw={repr(body)[:500]}", body,
                               usage=usage, metering=metering) from error


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


def estimate_transcription(price: AudioPrice | None, usage: TokenUsage | DurationUsage | None) -> dict | None:
    """Use reported metering only; never infer a billed duration from the WAV."""
    if price is None or usage is None or price.type != usage.type:
        return None
    with localcontext() as context:
        context.prec = 40
        if isinstance(price, DurationPrice):
            amount = price.per_second * Decimal(str(usage.seconds))
        else:
            if price.input_audio == price.input_text:
                inputs = usage.input_tokens * price.input_audio
            else:
                details = usage.input_token_details
                if (details is None or details.audio_tokens is None or details.text_tokens is None
                        or details.audio_tokens + details.text_tokens != usage.input_tokens):
                    return None
                inputs = details.audio_tokens * price.input_audio + details.text_tokens * price.input_text
            amount = (inputs + usage.output_tokens * price.output) / Decimal(1_000_000)
    return {"basis": "configured_estimate", "currency": price.currency, "amount": format(amount, "f")}
