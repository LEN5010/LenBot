"""Rootless Docker-backed Python worker.

The worker intentionally has no host-execution fallback.  A deployment must
explicitly provide the configured container runtime before enabling it.
"""
from __future__ import annotations

import asyncio
import os
import re
import signal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    runtime_path: str = "/usr/bin/docker"
    image: str = "python:3.13-slim"
    root_directory: str = ""
    timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_output_chars: int = Field(default=12000, ge=100, le=100000)
    max_artifact_bytes: int = Field(default=20_000_000, ge=1, le=500_000_000)
    container_user: str = '65532:65532'
    memory: str = "512m"
    cpus: str = "1.0"

    @field_validator("runtime_path")
    @classmethod
    def container_runtime_only(cls, value: str) -> str:
        if Path(value).name not in {"docker", "podman"}:
            raise ValueError("runtime_path must point to docker or podman")
        return value


class WorkspaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    workspace_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class RunPythonRequest(WorkspaceRequest):
    script: str = Field(min_length=1, max_length=100_000)


class FileRequest(WorkspaceRequest):
    path: str = Field(min_length=1, max_length=240)


class ExportRequest(FileRequest):
    """A separately named export operation for an operator-approved file."""


_SAFE_PATH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*$")


class WorkspaceWorker:
    def __init__(self, config: WorkspaceConfig, data_directory: Path):
        self.config = config
        self.root = Path(config.root_directory).expanduser() if config.root_directory else data_directory / "workspaces"

    def directory(self, workspace_id: str) -> Path:
        path = (self.root / workspace_id).resolve()
        root = self.root.resolve()
        if root not in path.parents:
            raise ValueError("workspace path escapes configured root")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def file_path(self, workspace_id: str, relative: str) -> Path:
        if not _SAFE_PATH.fullmatch(relative) or relative in {".", ".."}:
            raise ValueError("path must be a relative workspace path")
        path = (self.directory(workspace_id) / relative).resolve()
        if self.directory(workspace_id) not in path.parents:
            raise ValueError("file path escapes workspace")
        return path

    async def run_python(self, request: RunPythonRequest) -> dict:
        directory = self.directory(request.workspace_id)
        script = directory / ".lenbot_task.py"
        script.write_text(request.script, encoding="utf-8")
        command = [self.config.runtime_path, "run", "--rm", "--network", "none",
                   "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--user", self.config.container_user, "--pids-limit", "128", "--memory", self.config.memory, "--cpus", self.config.cpus,
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-v", f"{directory}:/workspace:rw", "-w", "/workspace", self.config.image,
                   "python", ".lenbot_task.py"]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True)
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.config.timeout_seconds)
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        except FileNotFoundError:
            return {"status": "unsupported", "error": "configured container runtime is unavailable"}
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    process.kill()
            await process.wait()
            return {"status": "error", "error": "Python worker timed out"}
        return {"status": "ok" if process.returncode == 0 else "error", "returncode": process.returncode,
                "stdout": stdout.decode("utf-8", "replace")[-self.config.max_output_chars:],
                "stderr": stderr.decode("utf-8", "replace")[-self.config.max_output_chars:],
                "workspace_id": request.workspace_id}

    def list_files(self, request: WorkspaceRequest) -> dict:
        directory = self.directory(request.workspace_id)
        files = [str(path.relative_to(directory)) for path in sorted(directory.rglob("*"))
                 if path.is_file() and not path.is_symlink()]
        return {"workspace_id": request.workspace_id, "files": files[:1000]}

    def read_file(self, request: FileRequest) -> dict:
        path = self.file_path(request.workspace_id, request.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("workspace file does not exist")
        data = path.read_text(encoding="utf-8", errors="replace")
        return {"workspace_id": request.workspace_id, "path": request.path,
                "content": data[:self.config.max_output_chars], "truncated": len(data) > self.config.max_output_chars}
