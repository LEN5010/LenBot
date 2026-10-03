"""Record a browser-produced task file and its source page, without copying it."""

from contextlib import contextmanager
import os
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .task_resources import ResourceFileRef, ResourceLocation, TaskResources
from .task_materials import require_name, require_directory
from .task_storage import DIRECTORY_FLAGS
from .tasks_config import WorkerSettings
from .tasks_store import Task, TaskStore


class BrowserOutput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    path: str
    kind: Literal['download', 'screenshot', 'pdf']
    page_url: str | None = Field(default=None, min_length=1)
    page_title: str | None = None
    download_url: str | None = None

    @field_validator('path')
    @classmethod
    def output_path(cls, value: str) -> str:
        ResourceLocation.relative_path(value)
        if not value.startswith('out/browser/'):
            raise ValueError(f'Browser output must be within out/browser/: {value!r}')
        return value

    @classmethod
    def parse(cls, raw: bytes) -> 'BrowserOutput':
        try:
            return cls.model_validate_json(raw)
        except ValidationError as error:
            raise ValueError(f'Invalid browser file result: {raw[:500]!r}; {error}') from error


def record_browser_output(settings: WorkerSettings, records: TaskStore, item: Task,
                          output: BrowserOutput) -> dict:
    reference = ResourceFileRef(scope='workspace', task_id=item.id, path=output.path)
    opened = TaskResources(settings, records).open(item.scene, reference)
    with opened.stream:
        result = {'name': opened.name, 'size': opened.size, 'mime_type': opened.mime_type,
                  'path': f'/workspace/{output.path}', 'reference': reference.model_dump(),
                  'source': output.model_dump(exclude={'path'})}
    records.add_event(item.scene, item.id, 'browser_file', result)
    return result


@contextmanager
def browser_output_file(settings: WorkerSettings, item: Task, kind: str, name: str):
    """Write one host-received output under the task, publishing only complete bytes."""
    require_name(name)
    root = settings.workspace_root / item.scene / 'tasks' / str(item.id)
    require_directory(root)
    directory = os.open(root, DIRECTORY_FLAGS)
    leaf = f'{kind}-{uuid4().hex}'
    relative = f'out/browser/{leaf}'
    try:
        for part in ('out', 'browser'):
            try:
                os.mkdir(part, mode=0o755, dir_fd=directory)
            except FileExistsError:
                pass
            nested = os.open(part, DIRECTORY_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = nested
        os.mkdir(leaf, mode=0o755, dir_fd=directory)
        nested = os.open(leaf, DIRECTORY_FLAGS, dir_fd=directory)
        os.close(directory)
        directory = nested
        descriptor = os.open('.partial', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644, dir_fd=directory)
        try:
            with os.fdopen(descriptor, 'wb') as output:
                yield f'{relative}/{name}', output
            os.rename('.partial', name, src_dir_fd=directory, dst_dir_fd=directory)
        except BaseException:
            os.unlink('.partial', dir_fd=directory)
            raise
    finally:
        os.close(directory)
