"""Offline task-tree relocation without rewriting conversations, task rows or Pi sessions."""

from contextlib import closing
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

from ..config import HostConfig, _load_host_source, _read_root
from ..instance_lock import instance_lock
from ..storage.pool import inspect_pool, worker_pool_usage
from ..configuration.storage_pool import StoragePool
from ..storage.store import FORMAT_VERSION


def configured_paths(value: object, prefix: str = '') -> Iterator[tuple[str, Path]]:
    if isinstance(value, Path):
        yield prefix, value
    elif isinstance(value, dict):
        for name, child in value.items():
            yield from configured_paths(child, f'{prefix}.{name}' if prefix else name)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from configured_paths(child, f'{prefix}[{index}]')


def stopped_tasks(config: HostConfig, sources: tuple[Path, Path]) -> None:
    if not config.database.exists():
        return
    with closing(sqlite3.connect(config.database.as_uri() + '?mode=ro', uri=True)) as db:
        version = (db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0])
        if version != (0x4C424E31, FORMAT_VERSION):
            raise ValueError(f'Task storage move needs the current database format: {version!r}')
        active = db.execute("SELECT scene,id,status,container,browser_active FROM tasks WHERE "
                            "status IN ('running','waiting_input') OR container IS NOT NULL OR browser_active=1").fetchall()
        if active:
            raise ValueError(f'Finish task processes and browser sessions before moving their files: {active!r}')
        for id, raw in db.execute('SELECT id,path FROM task_files'):
            path = Path(raw)
            if any(path.is_relative_to(source) for source in sources):
                raise ValueError(f'Registered delivery #{id} is inside a source task tree: {path}; move it to the independent delivery root first')


def preserve_owner(source: Path, target: Path) -> None:
    original, copied = source.lstat(), target.lstat()
    if (original.st_uid, original.st_gid) != (copied.st_uid, copied.st_gid):
        os.chown(target, original.st_uid, original.st_gid, follow_symlinks=False)


def copy_trees(bindings: tuple[tuple[Path, Path], ...], stage: Path) -> dict:
    links: dict[tuple[int, int], Path] = {}
    result = {'files': 0, 'file_bytes': 0, 'hardlinks': 0, 'symlinks': 0}

    def copy_node(original: Path, copied: Path) -> None:
        info = original.lstat()
        if stat.S_ISDIR(info.st_mode):
            copied.mkdir(mode=0o700)
            for child in original.iterdir():
                copy_node(child, copied / child.name)
        elif stat.S_ISLNK(info.st_mode):
            link = os.readlink(original)
            if os.path.isabs(link):
                absolute = Path(os.path.normpath(link))
                for old, new in bindings:
                    if absolute.is_relative_to(old):
                        link = str(new / absolute.relative_to(old))
                        break
            copied.symlink_to(link)
            result['symlinks'] += 1
        elif stat.S_ISREG(info.st_mode):
            key = info.st_dev, info.st_ino
            if key in links:
                os.link(links[key], copied)
                result['hardlinks'] += 1
            else:
                shutil.copyfile(original, copied)
                links[key] = copied
            result['files'] += 1
            result['file_bytes'] += info.st_size
        else:
            raise ValueError(f'Task storage move does not copy a special file: {original}; mode={oct(info.st_mode)}')
        preserve_owner(original, copied)
        shutil.copystat(original, copied, follow_symlinks=False)

    for source, destination in bindings:
        target = stage / destination.name
        if source.exists():
            copy_node(source, target)
        else:
            target.mkdir()
            preserve_owner(destination, target)
            shutil.copystat(destination, target)
    return result


def move_to_pool(root: Path, pool: StoragePool) -> dict:
    with instance_lock(root):
        path, source = _read_root(root)
        preserve_owner(path, root / '.lenbot-instance.lock')
        config = _load_host_source(path, deepcopy(source))
        if config.worker is None:
            raise ValueError('Configure worker before moving its task storage')
        worker = config.worker
        sources = worker.workspace_root, worker.runtime_root
        bindings = tuple(zip(sources, (pool.mount / 'workspaces', pool.mount / 'runtime')))
        for old in sources:
            for _, new in bindings:
                if old.is_relative_to(new) or new.is_relative_to(old):
                    raise ValueError(f'Task storage source and target overlap: {old}; {new}')
        candidate = deepcopy(source)
        candidate['worker'].update(storage_pool=pool.model_dump(mode='json'),
                                   workspace_root=str(bindings[0][1]), runtime_root=str(bindings[1][1]))
        target_config = _load_host_source(path, deepcopy(candidate))
        for name, referenced in configured_paths(target_config.model_dump()):
            if any(referenced.is_relative_to(old) for old in sources):
                raise ValueError(f'Task storage move would remove another configured path: {name}={referenced}')
        if any(root.is_relative_to(old) for old in sources):
            raise ValueError('The instance root cannot be inside a task tree being moved')
        stopped_tasks(config, sources)
        usage = inspect_pool(pool, timeout=worker.command_timeout_seconds)
        for _, destination in bindings:
            if not destination.is_dir() or any(destination.iterdir()):
                raise ValueError(f'Pool target must be an existing empty directory: {destination}')
        progress = {'copied_roots': [], 'config_switched': False, 'removed_sources': []}
        try:
            with tempfile.TemporaryDirectory(prefix='.lenbot-pool-move-', dir=pool.mount) as temporary:
                stage = Path(temporary)
                copied = copy_trees(bindings, stage)
                descriptor, config_name = tempfile.mkstemp(prefix='.lenbot-config-', suffix='.json', dir=root)
                config_file = Path(config_name)
                try:
                    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                        json.dump(candidate, output, ensure_ascii=False, allow_nan=False, indent=2)
                        output.write('\n')
                        output.flush()
                        os.fsync(output.fileno())
                    preserve_owner(path, config_file)
                    config_file.chmod(stat.S_IMODE(path.stat().st_mode))
                    for _, destination in bindings:
                        os.replace(stage / destination.name, destination)
                        progress['copied_roots'].append(str(destination))
                    worker_pool_usage(target_config.worker)
                    os.replace(config_file, path)
                    progress['config_switched'] = True
                finally:
                    config_file.unlink(missing_ok=True)
            for original in sources:
                if original.exists():
                    shutil.rmtree(original)
                    progress['removed_sources'].append(str(original))
        except BaseException as error:
            error.add_note(f'Task storage move state: {progress!r}; source roots={[str(p) for p in sources]!r}')
            raise
        return {**progress, **copied, 'storage_pool': pool.model_dump(mode='json'),
                'source_roots': [str(p) for p in sources], 'pool_before_move': asdict(usage),
                'notice': 'Task trees moved and root config switched. Database, Pi session contents and independent deliveries are unchanged.'}
