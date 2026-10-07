"""Load a descriptive role package without changing the running role."""

from __future__ import annotations

from bisect import bisect_right
from itertools import accumulate
from math import fsum, isclose
from pathlib import Path
from random import random
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .knowledge import PersonaDocument, load_knowledge
from .stickers import PersonaSticker, load_stickers
from ...image_assets import OriginalImage
from .avatar import load_avatar


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class PersonaTarget(BaseModel):
    """The actual directory the operator read, not an arbitrary write destination."""
    model_config = STRICT
    directory: str = Field(min_length=1)


def require_persona_target(path: Path, directory: str) -> None:
    if str(path) != directory:
        raise ValueError(f'保存角色包绑定已变化，本次未操作文件；原选择={directory!r}，当前={str(path)!r}。请重读后明确选择。')


class Style(BaseModel):
    model_config = STRICT

    name: str
    weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    note: str | None = None

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("style name must not be blank")
        return value


class Example(BaseModel):
    model_config = STRICT

    context: str
    line: str
    tags: list[str] = Field(default_factory=list)


class Persona(BaseModel):
    model_config = STRICT

    id: str
    name: str
    brief: str
    behavior: str
    self_reference: list[str]
    aliases: list[str]
    tools: Literal["all"] | list[str]
    skills: Literal["all"] | list[str]
    styles: list[Style]
    voice: str
    boundaries: str
    examples: list[Example]
    example_tags: list[str] = Field(default_factory=list)
    knowledge: dict[str, PersonaDocument] = Field(default_factory=dict, exclude=True, repr=False)
    stickers: dict[str, PersonaSticker] = Field(default_factory=dict, exclude=True, repr=False)
    avatar: OriginalImage | None = Field(default=None, exclude=True, repr=False)

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("name must not be blank")
        return value

    @field_validator("aliases")
    @classmethod
    def nonblank_aliases(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("aliases must not contain blank entries")
        return values

    @field_validator("example_tags")
    @classmethod
    def nonblank_example_tags(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("example_tags must not contain blank entries")
        return values

    @model_validator(mode="after")
    def known_example_tags(self) -> Persona:
        available = {tag for example in self.examples for tag in example.tags}
        unknown = [tag for tag in self.example_tags if tag not in available]
        if unknown:
            raise ValueError(f"example_tags not present in examples.yaml: {unknown!r}")
        return self

    @model_validator(mode="after")
    def complete_style_probability(self) -> Persona:
        if self.styles:
            total = fsum(style.weight for style in self.styles)
            if not isclose(total, 1.0, rel_tol=0, abs_tol=1e-12):
                raise ValueError(f"styles weights must sum to 1, got {total!r}")
        return self


def select_examples(persona: Persona) -> list[Example]:
    """Use the operator's exact tags, or the original first eight by default."""
    if not persona.example_tags:
        return persona.examples[:8]
    wanted = set(persona.example_tags)
    return [example for example in persona.examples if wanted.intersection(example.tags)][:8]


def select_style(persona: Persona) -> Style | None:
    """Sample one validated per-turn style without normalizing its probabilities."""
    if not persona.styles:
        return None
    positive = [style for style in persona.styles if style.weight > 0]
    endpoints = list(accumulate(style.weight for style in positive))
    endpoints[-1] = 1.0
    return positive[bisect_right(endpoints, random())]


PERSONA_FILES = ("persona.yaml", "voice.md", "boundaries.md", "examples.yaml")


def read_persona_files(path: Path) -> dict[str, str]:
    return {name: (path / name).read_text(encoding="utf-8") for name in PERSONA_FILES}


def _parse_yaml(path: Path, content: str) -> object:
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as error:
        raise ValueError(f"{path}: invalid YAML: {error}") from error


def parse_persona_files(path: Path, files: dict[str, str], *,
                        stickers: dict[str, PersonaSticker] | None = None) -> Persona:
    """Validate the same complete package for loading and an editor candidate."""
    path = path.resolve()
    metadata = _parse_yaml(path / "persona.yaml", files["persona.yaml"])
    if not isinstance(metadata, dict):
        raise ValueError(f"{path / 'persona.yaml'}: expected a YAML object")
    if any(field in metadata for field in ("voice", "boundaries", "examples", "knowledge", "stickers", 'avatar')):
        raise ValueError(f"{path / 'persona.yaml'}: voice, boundaries, examples, knowledge, stickers and avatar belong in separate files")

    examples = _parse_yaml(path / "examples.yaml", files["examples.yaml"])
    if not isinstance(examples, list):
        raise ValueError(f"{path / 'examples.yaml'}: expected a YAML list")
    try:
        persona = Persona.model_validate({
            **metadata,
            "voice": files["voice.md"],
            "boundaries": files["boundaries.md"],
            "examples": examples,
        })
        return persona.model_copy(update={"knowledge": load_knowledge(path),
                                          "stickers": load_stickers(path) if stickers is None else stickers,
                                          'avatar': load_avatar(path)})
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise ValueError(f"{path}: invalid persona package: {details}") from error


def load_persona(path: Path) -> Persona:
    """Load one package, preserving its complete validated example list."""
    return parse_persona_files(path, read_persona_files(path))
