"""Model providers, role bindings and prices in the root configuration."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from ..models.asr import ASRBinding
from .types import STRICT
from ..models.pricing import ModelPrice


class Provider(BaseModel):
    model_config = STRICT

    api: Literal["openai-chat", "openai-audio", "openai-embeddings"]
    base_url: str
    api_key: str = Field(repr=False)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.netloc
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment):
            raise ValueError("must be an HTTP(S) URL without credentials, query or fragment")
        return value

    @field_validator("api_key")
    @classmethod
    def required_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class Binding(BaseModel):
    model_config = STRICT

    provider: str
    model: str
    context_window_tokens: int = Field(gt=0)
    temperature: float = Field(default=0.6, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    reasoning_effort: str | None = None
    history_policy: Literal["native", "omit-reasoning"] = "native"

    @field_validator("provider", "model")
    @classmethod
    def required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("reasoning_effort")
    @classmethod
    def valid_reasoning_effort(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def output_fits_window(self) -> Binding:
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("max_output_tokens must be less than context_window_tokens")
        return self


class Roles(BaseModel):
    model_config = STRICT

    mind: Binding
    vision: Binding | None = None
    memory: Binding | None = None
    worker: Binding | None = None
    learner: Binding | None = None
    asr: ASRBinding | None = None


class Models(BaseModel):
    model_config = STRICT

    providers: dict[str, Provider]
    roles: Roles
    prices: dict[str, dict[str, ModelPrice]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def known_providers(self) -> Models:
        for role in ("mind", "vision", "memory", "worker", "learner", "asr"):
            binding = getattr(self.roles, role)
            if binding is None:
                continue
            provider_name = binding.provider
            if provider_name not in self.providers:
                raise ValueError(f"models.roles.{role}.provider references unknown provider {provider_name!r}")
            if role != "asr" and self.providers[provider_name].api != "openai-chat":
                raise ValueError(f"models.roles.{role} requires an openai-chat provider")
            if role == "asr" and self.providers[provider_name].api == "openai-embeddings":
                raise ValueError("models.roles.asr cannot use an embeddings-only provider")
        for provider_name, model_prices in self.prices.items():
            if not provider_name.strip():
                raise ValueError("models.prices provider name must not be blank")
            if provider_name not in self.providers:
                raise ValueError(f"models.prices references unknown provider {provider_name!r}")
            if any(not model_name.strip() for model_name in model_prices):
                raise ValueError(f"models.prices.{provider_name} model name must not be blank")
        return self
