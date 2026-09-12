"""Rootless Docker-backed Python worker.

The worker intentionally has no host-execution fallback.  A deployment must
explicitly provide the configured container runtime before enabling it.
"""
from __future__ import annotations

import asyncio
import codecs
import os
import re
import signal
import uuid
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
    max_artifact_files: int = Field(default=1000, ge=1, le=10000)
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
        self.control_root = self.root.parent / ".controls"
        self._run_locks: dict[str, asyncio.Lock] = {}

    def directory(self, workspace_id: str) -> Path:
        root = self.root.resolve()
        candidate = self.root / workspace_id
        if candidate.is_symlink():
            raise ValueError("workspace directory cannot be a symlink")
        path = candidate.resolve()
        if root not in path.parents:
            raise ValueError("workspace path escapes configured root")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def control_directory(self, workspace_id: str) -> Path:
        self.control_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = (self.control_root / workspace_id).resolve()
        root = self.control_root.resolve()
        if root not in path.parents:
            raise ValueError("control path escapes configured root")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    def run_lock(self, workspace_id: str) -> asyncio.Lock:
        return self._run_locks.setdefault(workspace_id, asyncio.Lock())

    @staticmethod
    def _write_control(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def file_path(self, workspace_id: str, relative: str) -> Path:
        if not _SAFE_PATH.fullmatch(relative) or relative in {".", ".."}:
            raise ValueError("path must be a relative workspace path")
        directory = self.directory(workspace_id)
        candidate = directory / relative
        current = directory
        for part in Path(relative).parts[:-1]:
            current = current / part
            if current.is_symlink():
                raise ValueError("workspace path cannot traverse symlinks")
        if candidate.is_symlink():
            raise ValueError("workspace file cannot be a symlink")
        path = candidate.resolve()
        if directory not in path.parents:
            raise ValueError("file path escapes workspace")
        return path

    async def run_python(self, request: RunPythonRequest) -> dict:
        async with self.run_lock(request.workspace_id):
            return await self._run_python_locked(request)

    async def _run_python_locked(self, request: RunPythonRequest) -> dict:
        directory = self.directory(request.workspace_id)
        control = self.control_directory(request.workspace_id)
        script = control / "task.py"
        self._write_control(script, request.script)
        container_name = f"lenbot-{request.workspace_id[:35]}-{uuid.uuid4().hex[:12]}"
        command = [self.config.runtime_path, "run", "--rm", "--network", "none",
                   "--name", container_name,
                   "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--user", self.config.container_user, "--pids-limit", "128", "--memory", self.config.memory, "--cpus", self.config.cpus,
                   "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m", "-v", f"{directory}:/workspace:rw",
                   "-v", f"{control}:/lenbot-control:ro", "-w", "/workspace", self.config.image,
                   "python", "/lenbot-control/task.py"]
        process = None
        try:
            process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, start_new_session=True)
            stdout_task = asyncio.create_task(self._read_limited(process.stdout))
            stderr_task = asyncio.create_task(self._read_limited(process.stderr))
            timed_out = False
            exceeded = False
            try:
                deadline = asyncio.get_running_loop().time() + self.config.timeout_seconds
                while process.returncode is None:
                    usage_bytes, usage_files = self._usage(directory)
                    if usage_bytes > self.config.max_artifact_bytes or usage_files > self.config.max_artifact_files:
                        exceeded = True
                        await self._terminate(container_name, process)
                        break
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        timed_out = True
                        await self._terminate(container_name, process)
                        break
                    try:
                        await asyncio.wait_for(process.wait(), min(remaining, 0.25))
                    except asyncio.TimeoutError:
                        continue
            except asyncio.CancelledError:
                await asyncio.shield(self._terminate(container_name, process))
                await asyncio.shield(asyncio.gather(stdout_task, stderr_task, return_exceptions=True))
                raise
            await process.wait()
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
        except FileNotFoundError:
            return {"status": "unsupported", "error": "configured container runtime is unavailable"}
        result_status = "error" if timed_out or exceeded or process.returncode else "ok"
        error = "Python worker timed out" if timed_out else "产物超过配置的字节或文件数上限" if exceeded else None
        return {"status": result_status, "returncode": process.returncode,
                "stdout": stdout, "stderr": stderr,
                "stdout_truncated": stdout_truncated, "stderr_truncated": stderr_truncated,
                "workspace_id": request.workspace_id, **({"error": error} if error else {})}

    async def _read_limited(self, stream):
        chunks: list[bytes] = []
        total = 0
        truncated = False
        while True:
            data = await stream.read(4096)
            if not data:
                break
            if total < self.config.max_output_chars:
                keep = min(len(data), self.config.max_output_chars - total)
                chunks.append(data[:keep])
                total += keep
                if keep < len(data):
                    truncated = True
            else:
                truncated = True
        return b"".join(chunks).decode("utf-8", "replace"), truncated

    @staticmethod
    def _usage(directory: Path) -> tuple[int, int]:
        total = 0
        count = 0
        for path in directory.rglob("*"):
            if path.is_file() and not path.is_symlink():
                try:
                    total += path.stat().st_size
                    count += 1
                except OSError:
                    pass
        return total, count

    async def _terminate(self, container_name: str, process) -> None:
        if process is None:
            return
        try:
            killer = await asyncio.create_subprocess_exec(self.config.runtime_path, "kill", container_name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(killer.wait(), timeout=5)
        except (OSError, asyncio.TimeoutError):
            pass
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
        try:
            remover = await asyncio.create_subprocess_exec(self.config.runtime_path, "rm", "-f", container_name,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(remover.wait(), timeout=5)
        except (OSError, asyncio.TimeoutError):
            pass

    def list_files(self, request: WorkspaceRequest) -> dict:
        directory = self.directory(request.workspace_id)
        files = [str(path.relative_to(directory)) for path in sorted(directory.rglob("*"))
                 if path.is_file() and not path.is_symlink()]
        return {"workspace_id": request.workspace_id, "files": files[:self.config.max_artifact_files],
                "truncated": len(files) > self.config.max_artifact_files}

    def read_file(self, request: FileRequest) -> dict:
        data, next_offset = self.read_text_range(request, 0, self.config.max_output_chars)
        return {"workspace_id": request.workspace_id, "path": request.path,
                "content": data, "truncated": next_offset is not None}

    def read_bytes(self, request: FileRequest, limit: int | None = None) -> bytes:
        path = self.file_path(request.workspace_id, request.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("workspace file does not exist")
        maximum = self.config.max_artifact_bytes if limit is None else min(limit, self.config.max_artifact_bytes)
        with path.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("产物超过配置的字节上限")
        return data

    def read_text_range(self, request: FileRequest, offset: int, limit: int) -> tuple[str, int | None]:
        if offset < 0 or limit < 1:
            raise ValueError("invalid text range")
        path = self.file_path(request.workspace_id, request.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("workspace file does not exist")
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        skip, collected = offset, []
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    text = decoder.decode(b"", final=True)
                    if skip < len(text):
                        collected.append(text[skip:])
                    break
                text = decoder.decode(chunk, final=False)
                if skip >= len(text):
                    skip -= len(text)
                    continue
                if skip:
                    text, skip = text[skip:], 0
                collected.append(text)
                if sum(map(len, collected)) > limit:
                    break
        value = "".join(collected)
        truncated = len(value) > limit
        value = value[:limit]
        return value, offset + len(value) if truncated else None
