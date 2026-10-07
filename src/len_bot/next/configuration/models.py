"""Model providers and role bindings in the root configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from ..models.asr import ASRBinding
from ..models.client import ModelSettings
from ..models.providers import ProviderAPI, CHAT_APIS, valid_url
from .types import STRICT


class Provider(BaseModel):
    model_config = STRICT

    api: ProviderAPI
    base_url: str
    api_key: str = Field(repr=False)
    proxy: str | None = Field(default=None, repr=False)

    @field_validator("base_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        return valid_url(value)

    @field_validator("proxy")
    @classmethod
    def valid_proxy(cls, value: str | None) -> str | None:
        return None if value is None else valid_url(value)

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
    temperature: float | None = Field(default=0.6, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=1024, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0, allow_inf_nan=False)
    reasoning_effort: str | None = None
    thinking_budget_tokens: int | None = Field(default=None, ge=0)
    output_token_field: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
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

    @model_validator(mode="after")
    def known_providers(self) -> Models:
        for role in ("mind", "vision", "memory", "worker", "learner", "asr"):
            binding = getattr(self.roles, role)
            if binding is None:
                continue
            provider_name = binding.provider
            if provider_name not in self.providers:
                raise ValueError(f"models.roles.{role}.provider references unknown provider {provider_name!r}")
            if role != "asr" and self.providers[provider_name].api not in CHAT_APIS:
                raise ValueError(f"models.roles.{role} requires a chat protocol provider")
            if role != "asr":
                provider = self.providers[provider_name]
                ModelSettings.from_binding(provider, binding)
                if provider.api != 'openai-chat' and binding.history_policy != 'native':
                    raise ValueError('原生协议必须保留原生历史续接')
            if role == "asr" and self.providers[provider_name].api not in {"openai-chat", "openai-audio"}:
                raise ValueError("models.roles.asr requires an openai-chat or openai-audio provider")
        return self
