"""Public, task-scoped execution request and artifact models."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RunPythonInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    script: str = Field(min_length=1, max_length=100_000)
    input_result_ids: list[str] = Field(default_factory=list, max_length=8)


class WorkspaceFileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: str = Field(min_length=1, max_length=240)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12_000, ge=1, le=100_000)


class ListWorkspaceInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class WorkspaceScope(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)
    scene_id: str
    requester_qq_uid: str
    job_id: str

    @property
    def workspace_id(self) -> str:
        # These identifiers are already constrained by the job/event contracts;
        # preserve ownership without a content hash or model-selected owner.
        scene = self.scene_id.replace(':', '_')
        user = self.requester_qq_uid.replace(':', '_')
        return f'{scene}__{user}__{self.job_id}'


class WorkspaceArtifact(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    path: str
    size_bytes: int = Field(ge=0)
    media_type: str = 'application/octet-stream'
    result_id: str | None = None
