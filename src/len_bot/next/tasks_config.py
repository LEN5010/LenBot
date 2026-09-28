"""Root-file task capacity, sandbox, and scene permission settings."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .pricing import Rate


STRICT = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
TaskRole = Literal["owner", "admin", "group_manager", "whitelist", "member"]


class EgressSettings(BaseModel):
    model_config = STRICT

    enabled: bool = True
    max_task_bytes: int = Field(default=500 * 1024 * 1024, gt=0, strict=True)
    max_scene_daily_bytes: int = Field(default=2 * 1024 * 1024 * 1024, gt=0, strict=True)
    max_connections: int = Field(default=16, gt=0, strict=True)
    bytes_per_second: int = Field(default=8 * 1024 * 1024, gt=0, strict=True)
    connect_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    header_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)


class WorkerSettings(BaseModel):
    model_config = STRICT

    docker_binary: Path
    docker_host: str
    image: str
    workspace_root: Path
    runtime_root: Path
    delivery_root: Path
    skills_directory: Path | None = None
    uid: int = Field(gt=0, strict=True)
    gid: int = Field(gt=0, strict=True)
    cpus: float = Field(default=2.0, gt=0, allow_inf_nan=False)
    memory: str = Field(default="2g", pattern=r"^[1-9][0-9]*[kmgKMG]$")
    pids_limit: int = Field(default=512, gt=0, strict=True)
    command_timeout_seconds: float = Field(default=30.0, gt=0, allow_inf_nan=False)
    max_running: int = Field(default=4, gt=0, strict=True)
    max_containers: int = Field(default=8, gt=0, strict=True)
    max_scene_containers: int = Field(default=4, gt=0, strict=True)
    max_calls: int = Field(default=40, gt=0, strict=True)
    max_request_bytes: int = Field(default=8 * 1024 * 1024, gt=0, strict=True)
    max_response_bytes: int = Field(default=64 * 1024 * 1024, gt=0, strict=True)
    max_cost: Rate | None = None
    compaction_reserve_tokens: int = Field(default=16384, gt=0, strict=True)
    compaction_keep_recent_tokens: int = Field(default=20000, gt=0, strict=True)
    active_timeout_seconds: float = Field(default=1800.0, gt=0, allow_inf_nan=False)
    input_timeout_seconds: float = Field(default=1800.0, gt=0, allow_inf_nan=False)
    max_file_bytes: int = Field(default=25 * 1024 * 1024, gt=0, strict=True)
    input_support: Literal["text", "text-image"] = "text"
    model_reasoning: bool
    egress: EgressSettings = Field(default_factory=EgressSettings)

    @field_validator("docker_binary", "workspace_root", "runtime_root", "delivery_root")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("must be an explicit absolute path")
        return value

    @field_validator("docker_host")
    @classmethod
    def local_docker_host(cls, value: str) -> str:
        endpoint = urlsplit(value)
        if (not value.startswith("unix:///") or endpoint.scheme != "unix" or endpoint.netloc
                or not Path(endpoint.path).is_absolute() or endpoint.query or endpoint.fragment):
            raise ValueError("docker_host must be an explicit local unix:///absolute/socket path")
        return value

    @field_validator("image")
    @classmethod
    def named_image(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("image must not be blank")
        return value

    @field_validator("skills_directory")
    @classmethod
    def absolute_skills_directory(cls, value: Path | None) -> Path | None:
        if value is not None and not value.is_absolute():
            raise ValueError("skills_directory must be an explicit absolute path")
        return value

    @model_validator(mode="after")
    def separate_task_trees(self) -> WorkerSettings:
        roots = {
            "workspace_root": self.workspace_root,
            "runtime_root": self.runtime_root,
            "delivery_root": self.delivery_root,
        }
        if self.skills_directory is not None:
            roots["skills_directory"] = self.skills_directory
        for first_name, first in roots.items():
            for second_name, second in roots.items():
                if first_name != second_name and first.is_relative_to(second):
                    raise ValueError(f"worker.{first_name} must not be inside worker.{second_name}")
        return self


class TaskSettings(BaseModel):
    model_config = STRICT

    enabled: bool = False
    owner: str | None = None
    admins: list[str] = Field(default_factory=list)
    whitelist: list[str] = Field(default_factory=list)
    delegate_roles: list[TaskRole] = Field(default_factory=lambda: ["owner", "admin", "whitelist"])
    manage_roles: list[TaskRole] = Field(default_factory=lambda: ["owner", "admin"])
    max_running: int = Field(default=2, gt=0, strict=True)
    max_daily_tasks: int = Field(default=5, gt=0, strict=True)
    egress_max_task_bytes: int | None = Field(default=None, gt=0, strict=True)
    egress_max_daily_bytes: int | None = Field(default=None, gt=0, strict=True)
    egress_bytes_per_second: int | None = Field(default=None, gt=0, strict=True)

    @field_validator("owner")
    @classmethod
    def valid_owner(cls, value: str | None) -> str | None:
        if value is not None and re.fullmatch(r"[1-9][0-9]*", value) is None:
            raise ValueError("owner must be a positive QQ number as text")
        return value

    @field_validator("admins", "whitelist")
    @classmethod
    def valid_qqs(cls, values: list[str]) -> list[str]:
        if any(re.fullmatch(r"[1-9][0-9]*", value) is None for value in values):
            raise ValueError("must contain positive QQ numbers as text")
        return values

    @field_validator("delegate_roles", "manage_roles")
    @classmethod
    def unique_roles(cls, values: list[TaskRole]) -> list[TaskRole]:
        if len(values) != len(set(values)):
            raise ValueError("task roles must not repeat")
        return values
