"""Private snapshots of explicitly selected scene files, never a live shared-folder mount."""

import os
from pathlib import Path
import shutil
import tempfile

from pydantic import BaseModel, model_validator

from ..configuration.types import STRICT
from .materials import MaterialName, list_materials, material_directory, open_regular, require_directory
from .resources import ResourceFileRef
from ..configuration.tasks import WorkerSettings


class ResourceInput(BaseModel):
    model_config = STRICT
    reference: ResourceFileRef
    name: MaterialName

    @model_validator(mode='after')
    def input_scope(self):
        if self.reference.scope == 'runtime':
            raise ValueError('运行环境文件不作为任务输入；选择工作文件、输入快照、交付或共享资料')
        return self


def create_stage(settings: WorkerSettings, scene: str) -> Path:
    parent = settings.runtime_root / scene
    require_directory(parent)
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    require_directory(parent)
    return Path(tempfile.mkdtemp(prefix='.task-input-stage-', dir=parent))


def copy_inputs(stage: Path, settings: WorkerSettings, scene: str, names: tuple[str, ...]) -> list[dict]:
    shared = material_directory(settings.workspace_root, scene)
    actual = {item['name'] for item in list_materials(shared)['files']}
    if missing := set(names) - actual:
        raise FileNotFoundError(f'Selected filenames are not in the actual scene material directory {shared}: {sorted(missing)!r}')
    copies = []
    for name in names:
        source, size = open_regular(shared / name)
        with source:
            if size > settings.max_file_bytes:
                raise ValueError(f'Task input exceeds worker.max_file_bytes: {name!r}; {size} > {settings.max_file_bytes}')
            descriptor = os.open(stage / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, 'wb') as output:
                remaining = size
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError(f'Shared input became shorter during snapshot: {shared / name}')
                    output.write(chunk)
                    remaining -= len(chunk)
                if source.read(1):
                    raise OSError(f'Shared input grew during snapshot: {shared / name}')
                output.flush()
                os.fsync(output.fileno())
        copies.append({'name': name, 'size': size, 'source_path': str(shared / name), 'container_path': f'/inputs/{name}',
                       'reference': ResourceFileRef(scope='shared', path=name).model_dump()})
    return copies


def publish_inputs(stage: Path, settings: WorkerSettings, scene: str, task_id: int, names: tuple[str, ...]) -> Path:
    destination = settings.runtime_root / scene / str(task_id) / 'inputs'
    require_directory(destination)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.mkdir(mode=0o700)
    for name in names:
        os.link(stage / name, destination / name, follow_symlinks=False)
    return destination


def remove_stage(stage: Path) -> None:
    shutil.rmtree(stage)


def require_inputs(root: Path, names: tuple[str, ...], max_bytes: int) -> None:
    snapshot = list_materials(root)
    actual = {item['name'] for item in snapshot['files']}
    if not snapshot['exists'] or actual != set(names):
        raise ValueError(f'Task input snapshot is missing or differs from its recorded selection: {root}; '
                         f'expected={names!r}, actual={sorted(actual)!r}')
    for item in snapshot['files']:
        if item['size'] > max_bytes:
            raise ValueError(f'Task input exceeds current worker.max_file_bytes: {root / item["name"]}; {item["size"]} > {max_bytes}')
