"""Parse structured development replay cases without inventing platform events."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from len_bot.next.config_types import EpochSeconds
from len_bot.next.messages import parse_message


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


class MessageStep(BaseModel):
    model_config = STRICT

    type: Literal["message"]
    event: dict[str, Any]


class ObserveStep(BaseModel):
    model_config = STRICT

    type: Literal["observe"]
    seconds: float = Field(gt=0, allow_inf_nan=False)


class AwaitTurnStep(BaseModel):
    model_config = STRICT

    type: Literal["await_turn"]
    count: int = Field(gt=0, strict=True)


class RestartStep(BaseModel):
    model_config = STRICT

    type: Literal["restart"]


ReplayStep = Annotated[MessageStep | ObserveStep | AwaitTurnStep | RestartStep, Field(discriminator="type")]


class InitialMemory(BaseModel):
    model_config = STRICT

    directory: Path
    jobs: Path

    @field_validator("directory", "jobs", mode="before")
    @classmethod
    def source_path(cls, value: object) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("memory snapshot source must be a nonblank path string")
        return Path(value)


class ReplayCase(BaseModel):
    model_config = STRICT

    id: str
    set: str
    start_time: EpochSeconds | None = None
    initial_database: Path | None = None
    initial_memory: InitialMemory | None = None
    web_materials: Path | None = None
    image_materials: Path | None = None
    memory_materials: Path | None = None
    expect: list[str] = Field(min_length=1)
    steps: list[ReplayStep] = Field(min_length=1)

    @model_validator(mode="after")
    def memory_has_history(self):
        if self.initial_memory is not None and self.initial_database is None:
            raise ValueError("initial_memory requires its matching initial_database")
        return self

    @field_validator("id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if CASE_ID.fullmatch(value) is None:
            raise ValueError(f"case id must be a single ASCII path segment: {value!r}")
        return value

    @field_validator("expect")
    @classmethod
    def nonblank_expectations(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("expect must contain nonblank original descriptions")
        return values

    @field_validator("initial_database", "web_materials", "image_materials", "memory_materials", mode="before")
    @classmethod
    def database_path_text(cls, value: object) -> Path | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("initial_database/web_materials/image_materials/memory_materials must be a nonblank path string")
        return Path(value)


class CaseFile(BaseModel):
    model_config = STRICT

    format: Literal["structured-development"]
    source: str
    cases: list[ReplayCase] = Field(min_length=1)

    @field_validator("source")
    @classmethod
    def nonblank_source(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source must not be blank")
        return value

    @model_validator(mode="after")
    def unique_case_ids(self) -> CaseFile:
        seen: dict[str, str] = {}
        for index, case in enumerate(self.cases):
            key = case.id.casefold()
            if key in seen:
                raise ValueError(
                    f"cases[{index}] replay case ids conflict on case-insensitive paths: "
                    f"{seen[key]!r} and {case.id!r}"
                )
            seen[key] = case.id
        return self


def _fragment(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)[:500]


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def load_cases(path: Path, *, set_name: str, scene: str, bot_qq: str) -> CaseFile:
    """Validate one JSON file and its original OneBot envelopes once at ingress."""
    path = Path(path)
    data = path.read_bytes()
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError as error:
        fragment = data[max(0, error.start - 50):error.end + 50]
        raise ValueError(f"{path}: invalid UTF-8: {error}; raw={fragment!r}") from error
    try:
        source = json.loads(raw, parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        fragment = raw[max(0, error.pos - 100):error.pos + 400]
        raise ValueError(
            f"{path}: invalid JSON at line {error.lineno}, column {error.colno}: "
            f"{error.msg}; raw={fragment[:500]!r}"
        ) from error
    except ValueError as error:
        raise ValueError(f"{path}: invalid JSON: {error}; raw={raw[:500]!r}") from error

    try:
        cases = CaseFile.model_validate(source)
    except ValidationError as error:
        problem = error.errors(include_input=False)[0]
        location = problem["loc"]
        item = source
        case_name = None
        if isinstance(source, dict) and len(location) >= 2 and location[0] == "cases" and isinstance(location[1], int):
            index = location[1]
            raw_cases = source.get("cases")
            if isinstance(raw_cases, list) and index < len(raw_cases):
                item = raw_cases[index]
                if isinstance(item, dict):
                    case_name = item.get("id")
        where = ".".join(map(str, location)) or "case file"
        label = "" if case_name is None else f" case={case_name!r}"
        raise ValueError(
            f"{path}:{label} {where}: {problem['msg']}; raw={_fragment(item)}"
        ) from error

    for index, case in enumerate(cases.cases):
        if case.set != set_name:
            raise ValueError(
                f"{path}: cases[{index}] id={case.id!r} set {case.set!r} differs from selected "
                f"set {set_name!r}; raw={_fragment(source['cases'][index])}"
            )
        if case.initial_database is not None:
            candidate = case.initial_database
            try:
                case.initial_database = (
                    candidate if candidate.is_absolute() else path.parent / candidate
                ).resolve()
            except (OSError, ValueError, RuntimeError) as error:
                raise ValueError(
                    f"{path}: cases[{index}] id={case.id!r} invalid initial_database: {error}; "
                    f"raw={_fragment(source['cases'][index])}"
                ) from error
        if case.initial_memory is not None:
            for name in ("directory", "jobs"):
                candidate = getattr(case.initial_memory, name)
                setattr(case.initial_memory, name,
                        (candidate if candidate.is_absolute() else path.parent / candidate).resolve())
        for name in ('web_materials', 'image_materials', 'memory_materials'):
            candidate = getattr(case, name)
            if candidate is not None:
                setattr(case, name, (candidate if candidate.is_absolute() else path.parent / candidate).resolve())
        for position, step in enumerate(case.steps):
            if not isinstance(step, MessageStep):
                continue
            try:
                message = parse_message(step.event, own_message_ids=set())
            except ValueError as error:
                raise ValueError(
                    f"{path}: cases[{index}] id={case.id!r} steps[{position}]: {error}"
                ) from error
            if message.scene != scene or str(step.event["self_id"]) != bot_qq:
                raise ValueError(
                    f"{path}: cases[{index}] id={case.id!r} steps[{position}] scene/self_id "
                    f"does not match configured {scene}/{bot_qq}; raw={_fragment(step.event)}"
                )
    return cases
