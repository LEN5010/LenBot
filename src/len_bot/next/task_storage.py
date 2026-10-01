"""Read current task-tree metadata without content access or link traversal."""

from dataclasses import asdict, dataclass
import os
from pathlib import Path
import stat
import shutil

from .tasks_config import WorkerSettings


DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


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
