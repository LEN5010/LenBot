"""Load a descriptive role package without changing the running role."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


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
    if any(field in metadata for field in ("voice", "boundaries", "examples")):
        raise ValueError(f"{path / 'persona.yaml'}: voice, boundaries and examples belong in separate files")

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
        return persona.model_copy(update={"examples": persona.examples[:8]})
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(map(str, item['loc']))}: {item['msg']}"
            for item in error.errors(include_input=False)
        )
        raise ValueError(f"{path}: invalid persona package: {details}") from error
