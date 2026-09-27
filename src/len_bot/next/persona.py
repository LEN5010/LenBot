"""Load a descriptive role package without changing the running role."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .persona_knowledge import PersonaDocument, load_knowledge


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Style(BaseModel):
    model_config = STRICT

    name: str
    weight: float = Field(ge=0, le=1, allow_inf_nan=False)
    note: str | None = None


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
    knowledge: dict[str, PersonaDocument] = Field(default_factory=dict, exclude=True, repr=False)

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


def _read_yaml(path: Path) -> object:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"{path}: invalid YAML: {error}") from error


def load_persona(path: Path) -> Persona:
    """Load one package; P1 selects its first at most eight examples in file order."""
    path = path.resolve()
    metadata = _read_yaml(path / "persona.yaml")
    if not isinstance(metadata, dict):
        raise ValueError(f"{path / 'persona.yaml'}: expected a YAML object")
    if any(field in metadata for field in ("voice", "boundaries", "examples", "knowledge")):
        raise ValueError(f"{path / 'persona.yaml'}: voice, boundaries, examples and knowledge belong in separate files")

    examples = _read_yaml(path / "examples.yaml")
    if not isinstance(examples, list):
        raise ValueError(f"{path / 'examples.yaml'}: expected a YAML list")
    try:
        persona = Persona.model_validate({
            **metadata,
            "voice": (path / "voice.md").read_text(encoding="utf-8"),
            "boundaries": (path / "boundaries.md").read_text(encoding="utf-8"),
            "examples": examples,
        })
        return persona.model_copy(update={
            "examples": persona.examples[:8],
            "knowledge": load_knowledge(path),
        })
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise ValueError(f"{path}: invalid persona package: {details}") from error
