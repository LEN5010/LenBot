"""Read current task-tree metadata without content access or link traversal."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import stat
import shutil

from .tasks_config import WorkerSettings
from .tasks_store import TERMINAL, Task


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


def continuation_state(item: Task, discarded: bool) -> str:
    if discarded:
        return 'discarded'
    if item.status not in TERMINAL or item.container is not None or item.browser_active:
        return 'active'
    return 'account_new_task' if item.account_browser else 'retained'


def temporary_paths(settings: WorkerSettings, scene: str, task_id: int) -> list[tuple[Path, str]]:
    workspace = settings.workspace_root / scene / 'tasks' / str(task_id)
    runtime = settings.runtime_root / scene / str(task_id)
    return [(runtime / 'control', 'directory'), (workspace / '.playwright', 'directory'),
            *((workspace / name, 'file') for name in ('pi.stderr', 'model-bridge.stderr', 'egress-bridge.stderr')),
            (runtime / 'home' / '.pi' / 'agent' / 'models.json', 'file')]


def temporary_usage(settings: WorkerSettings, scene: str, task_id: int) -> list[dict]:
    result = []
    for path, kind in temporary_paths(settings, scene, task_id):
        if path.resolve(strict=False) != path:
            raise ValueError(f'Task temporary path must not traverse a symbolic link: {path}')
        if kind == 'directory':
            usage = directory_usage(path)
            result.append({'path': str(path), 'kind': kind, 'exists': usage is not None,
                           'file_bytes': 0 if usage is None else usage.file_bytes})
        else:
            try:
                info = path.lstat()
            except FileNotFoundError:
                result.append({'path': str(path), 'kind': kind, 'exists': False, 'file_bytes': 0})
                continue
            if not stat.S_ISREG(info.st_mode):
                raise ValueError(f'Task temporary file is not a regular file: {path}')
            result.append({'path': str(path), 'kind': kind, 'exists': True, 'file_bytes': info.st_size})
    return result


def clean_temporary_files(settings: WorkerSettings, scene: str, task_id: int, progress: dict) -> None:
    for item in temporary_usage(settings, scene, task_id):
        if not item['exists']:
            progress['absent'].append(item['path'])
            continue
        progress['active_root'] = item['path']
        path = Path(item['path'])
        if item['kind'] == 'directory':
            shutil.rmtree(path)
        else:
            path.unlink()
        progress['removed'].append(item['path'])
        progress['removed_file_bytes'] += item['file_bytes']
    progress['active_root'] = None
    progress['complete'] = True


def output_entries(root: Path, path: str, offset: int, limit: int) -> dict:
    """List one actual output directory without opening file contents or links."""
    if root.resolve(strict=False) != root:
        raise ValueError(f'Task output root must not traverse a symbolic link: {root}')
    try:
        descriptor = os.open(root, DIRECTORY_FLAGS)
    except FileNotFoundError:
        return {'exists': False, 'entries': [], 'next_offset': None}
    try:
        if path:
            for part in path.split('/'):
                nested = os.open(part, DIRECTORY_FLAGS, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = nested
        with os.scandir(descriptor) as children:
            names = sorted(child.name for child in children)
        entries = []
        for name in names[offset:offset + limit]:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            kind = ('file' if stat.S_ISREG(info.st_mode) else
                    'directory' if stat.S_ISDIR(info.st_mode) else
                    'symlink' if stat.S_ISLNK(info.st_mode) else 'other')
            entries.append({'path': f'{path}/{name}' if path else name, 'kind': kind,
                            'size': info.st_size if kind == 'file' else None,
                            'modified': info.st_mtime})
        return {'exists': True, 'entries': entries,
                'next_offset': offset + limit if offset + limit < len(names) else None}
    finally:
        os.close(descriptor)


@dataclass
class TreeUsage:
    file_bytes: int = 0
    allocated_bytes: int = 0
    files: int = 0
    directories: int = 0
    links: int = 0
    special_files: int = 0
    hardlinked_files: int = 0


def directory_usage(root: Path) -> TreeUsage | None:
    if root.resolve(strict=False) != root:
        raise ValueError(f'Task storage root must not traverse a symbolic link: {root}')
    try:
        descriptor = os.open(root, DIRECTORY_FLAGS)
    except FileNotFoundError:
        return None
    usage = TreeUsage()
    allocated: set[tuple[int, int]] = set()

    def allocation(info: os.stat_result) -> None:
        identity = (info.st_dev, info.st_ino)
        if identity not in allocated:
            allocated.add(identity)
            usage.allocated_bytes += info.st_blocks * 512

    def scan(directory: int) -> None:
        allocation(os.fstat(directory))
        usage.directories += 1
        with os.scandir(directory) as children:
            for child in children:
                info = os.stat(child.name, dir_fd=directory, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    nested = os.open(child.name, DIRECTORY_FLAGS, dir_fd=directory)
                    try:
                        scan(nested)
                    finally:
                        os.close(nested)
                else:
                    allocation(info)
                    if stat.S_ISREG(info.st_mode):
                        usage.files += 1
                        usage.file_bytes += info.st_size
                        if info.st_nlink > 1:
                            usage.hardlinked_files += 1
                    elif stat.S_ISLNK(info.st_mode):
                        usage.links += 1
                    else:
                        usage.special_files += 1

    try:
        scan(descriptor)
    finally:
        os.close(descriptor)
    return usage


def storage_usage(settings: WorkerSettings, scene: str, task_id: int) -> list[dict]:
    roots = (
        ('workspace', settings.workspace_root / scene / 'tasks' / str(task_id)),
        ('runtime', settings.runtime_root / scene / str(task_id)),
        ('deliveries', settings.delivery_root / scene / str(task_id)),
    )
    result = []
    for kind, path in roots:
        try:
            usage = directory_usage(path)
        except (OSError, RuntimeError) as error:
            raise ValueError(f'Task storage scan failed at {path}: {type(error).__name__}: {error}') from error
        result.append({'kind': kind, 'path': str(path), 'exists': usage is not None,
                       'usage': None if usage is None else asdict(usage)})
    return result


def discard_task_trees(workspace: Path, runtime: Path, progress: dict) -> None:
    if not shutil.rmtree.avoids_symlink_attacks:
        raise RuntimeError('Current platform does not support descriptor-based task-tree removal')
    present = []
    for path in (workspace, runtime):
        if path.resolve(strict=False) != path:
            raise ValueError(f'Task removal root must not traverse a symbolic link: {path}')
        try:
            info = path.lstat()
        except FileNotFoundError:
            progress['absent'].append(str(path))
            continue
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError(f'Task removal root must be an actual directory: {path}; mode={oct(info.st_mode)}')
        present.append(path)
    for path in present:
        progress['active_root'] = str(path)
        shutil.rmtree(path)
        progress['removed'].append(str(path))
    progress['active_root'] = None
    progress['complete'] = True
