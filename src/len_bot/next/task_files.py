"""Task output browsing, copied deliveries and explicit environment removal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import traceback
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .sandbox import DockerSandbox, SandboxHandle
from .task_materials import finish_file_operation
from .task_storage import discard_task_trees, output_entries
from .tasks_config import WorkerSettings
from .tasks_store import Task, TaskFile, TaskStore


class DeliverFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=240)
    note: str

    @field_validator("name")
    @classmethod
    def filename(cls, value: str) -> str:
        if value in {".", ".."} or any(char in value for char in ("/", "\\", "\x00", "\r", "\n")):
            raise ValueError("name must be a filename, not a path")
        return value


def file_info(file: TaskFile, records: TaskStore) -> dict:
    return {"id": file.id, "task_id": file.task_id, "name": file.name,
            "size": file.size, "note": file.note, "status": "registered",
            "upload": records.latest_file_upload(file)}


class TaskFiles:
    """Disk operations; callers own task permissions and lifecycle serialization."""

    def __init__(self, settings: WorkerSettings, records: TaskStore, sandbox: DockerSandbox,
                 notify: Callable[[str], None]):
        self.settings, self.records, self.sandbox, self.notify = settings, records, sandbox, notify

    async def outputs(self, item: Task, *, path: str, offset: int, limit: int) -> dict:
        root = self.settings.workspace_root / item.scene / 'tasks' / str(item.id) / 'out'
        entries = await asyncio.to_thread(output_entries, root, path, offset, limit)
        return {'task_id': item.id, 'root': 'out', 'path': path, **entries,
                'registered_files': [file_info(file, self.records) for file in self.records.list_files(item.scene, item.id)],
                'notice': 'entries 是当前磁盘元数据，不是内容验证；registered_files 才是可下载交付，上传状态另列。'}

    async def deliver(self, item: Task, sandbox: SandboxHandle, raw: bytes) -> dict:
        arguments = DeliverFile.model_validate_json(raw)
        destination = self.settings.delivery_root / item.scene / str(item.id)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / uuid4().hex
        try:
            await self.sandbox.copy_out(sandbox, arguments.path, target,
                                        max_bytes=self.settings.max_file_bytes)
            file = self.records.add_file(item.scene, item.id, name=arguments.name, path=str(target),
                                         size=target.stat().st_size, note=arguments.note)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        self.records.add_event(item.scene, item.id, "file", file_info(file, self.records))
        self.notify(item.scene)
        return file_info(file, self.records)

    async def discard(self, item: Task, *, requester: str, workspace: str, runtime: str) -> dict:
        scene, id = item.scene, item.id
        expected_workspace = self.settings.workspace_root / scene / 'tasks' / str(id)
        expected_runtime = self.settings.runtime_root / scene / str(id)
        if workspace != str(expected_workspace) or runtime != str(expected_runtime):
            raise ValueError(f'Task removal roots differ from the current worker; expected='
                             f'{(str(expected_workspace), str(expected_runtime))!r}; received={(workspace, runtime)!r}')
        for file in self.records.registered_files():
            recorded = Path(file.path)
            actual = recorded.resolve(strict=False)
            if any(path.is_relative_to(root) for path in (recorded, actual)
                   for root in (expected_workspace, expected_runtime)):
                raise ValueError(f'Registered delivery would be removed by these current task roots; '
                                 f'scene={file.scene!r}, task={file.task_id}, file={file.id}, path={file.path!r}; '
                                 'retain/move the actual delivery explicitly before discarding this environment')
        self.records.add_event(scene, id, 'workspace_discard', {'requester': requester,
            'workspace': workspace, 'runtime': runtime, 'notice': '明确放弃此任务环境，不再续接；不是物理删除成功回执'})
        self.notify(scene)
        progress = {'complete': False, 'removed': [], 'absent': [], 'active_root': None, 'error': None}
        original_error: BaseException | None = None
        try:
            await finish_file_operation(discard_task_trees, expected_workspace, expected_runtime, progress)
        except BaseException as error:
            original_error = error
            progress['error'] = ''.join(traceback.format_exception_only(error)).strip()
            error.add_note(f'Task #{id} environment is permanently discarded; partial removal={progress!r}')
            raise
        finally:
            try:
                self.records.add_event(scene, id, 'workspace_discard_result', progress)
                self.notify(scene)
            except BaseException as record_error:
                if original_error is None:
                    record_error.add_note(f'Task #{id} environment is discarded; filesystem result={progress!r}')
                    raise
                original_error.add_note(f'Workspace removal result recording also failed: {type(record_error).__name__}: {record_error}')
        return progress
