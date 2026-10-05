"""Offline preservation of actual terminal task rows and native files; never a task restore."""

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
import tarfile
import time
import traceback

from ..config import HostConfig, LabConfig, load_instance_config
from ..instance_lock import instance_lock
from ..storage.store import FORMAT_VERSION, encode
from ..work.materials import open_regular, require_directory
from ..work.store import TERMINAL, TaskFile, _task


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def stopped(path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f'Media transfer requires an existing regular stopped SQLite file: {path}')
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f'Media transfer requires a complete checkpointed snapshot; nonempty {sidecar}')


def write_json(path: Path, value: object) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w', encoding='ascii') as output:
        output.write(json.dumps(value, ensure_ascii=True, allow_nan=False) + '\n')
        output.flush()
        os.fsync(output.fileno())


def read_metadata(config: HostConfig | LabConfig) -> dict:
    stopped(config.database)
    with closing(sqlite3.connect(config.database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        db.row_factory = sqlite3.Row
        actual = (db.execute('PRAGMA application_id').fetchone()[0], db.execute('PRAGMA user_version').fetchone()[0])
        if actual != (0x4C424E31, FORMAT_VERSION):
            raise ValueError(f'Task archive needs the explicitly migrated current database: {actual!r}')
        tasks = [dict(row) for row in db.execute('SELECT * FROM tasks WHERE scene IN '
                    '(SELECT value FROM json_each(?)) ORDER BY id', (encode(config.task_archive.scenes),))]
        for scene in config.task_archive.scenes:
            if not any(row['scene'] == scene for row in tasks):
                raise ValueError(f'Selected scene has no actual task records: {scene}')
        for raw in tasks:
            task = _task(raw)
            if task.status not in TERMINAL or task.container is not None or task.browser_active:
                raise ValueError(f'Task is not a stopped terminal record: scene={task.scene!r}, id={task.id}, '
                                 f'status={task.status}, container={task.container!r}, browser_active={task.browser_active}')
        result = {'tasks': tasks}
        for table in ('task_events', 'task_files'):
            result[table] = [dict(row) for row in db.execute(
                f'SELECT * FROM {table} WHERE scene IN (SELECT value FROM json_each(?)) ORDER BY id',
                (encode(config.task_archive.scenes),))]
            known = {(row['scene'], row['id']) for row in tasks}
            for row in result[table]:
                if (row['scene'], row['task_id']) not in known:
                    raise ValueError(f'Task archive row has no matching actual task: table={table}; raw={repr(row)[:1000]}')
                if table == 'task_events':
                    try:
                        json.loads(row['body'], parse_constant=_reject_constant)
                    except ValueError as error:
                        raise ValueError(f'Invalid task event body: {error}; raw={row["body"][:1000]!r}') from error
        return result


def add_node(archive: tarfile.TarFile, path: Path, member: str, result: dict) -> None:
    info = path.lstat()
    if stat.S_ISREG(info.st_mode):
        source, size = open_regular(path)
        with source:
            header = archive.gettarinfo(fileobj=source, arcname=member)
            archive.addfile(header, source if header.isreg() else None)
            if header.isreg() and source.read(1):
                raise OSError(f'Task file grew during archive: {path}')
        result['members'].append({'name': member, 'type': 'file' if header.isreg() else 'hardlink',
                                  'size': size, 'linkname': header.linkname})
    elif stat.S_ISDIR(info.st_mode):
        require_directory(path)
        archive.addfile(archive.gettarinfo(path, arcname=member))
        result['members'].append({'name': member, 'type': 'directory'})
        with os.scandir(path) as children:
            names = sorted(entry.name for entry in children)
        for name in names:
            add_node(archive, path / name, member + '/' + name, result)
    elif stat.S_ISLNK(info.st_mode):
        header = archive.gettarinfo(path, arcname=member)
        archive.addfile(header)
        result['members'].append({'name': member, 'type': 'symlink', 'linkname': header.linkname})
    else:
        result['metadata_only'].append({'name': member, 'mode': info.st_mode, 'uid': info.st_uid,
            'gid': info.st_gid, 'mtime_ns': info.st_mtime_ns, 'rdev': info.st_rdev,
            'notice': 'Special node metadata only; no pipe/device/socket content read or node creation.'})


def archive_tasks(config: HostConfig | LabConfig) -> dict:
    settings = config.task_archive
    if settings is None or config.worker is None:
        raise ValueError('Configure task_archive and the actual worker roots in the sole root lenbot.config.json')
    worker = config.worker
    destination = settings.destination
    if os.path.lexists(destination):
        raise FileExistsError(f'Task archive never overwrites or resumes a directory: {destination}')
    require_directory(destination)
    for root in (worker.workspace_root, worker.runtime_root, worker.delivery_root):
        if destination.is_relative_to(root) or root.is_relative_to(destination):
            raise ValueError(f'Task archive destination overlaps a source task tree: {destination}; source={root}')
    if config.database.is_relative_to(destination):
        raise ValueError('Task archive destination cannot contain the source database')
    metadata = read_metadata(config)
    for raw in metadata['task_files']:
        file = TaskFile(**raw)
        path = Path(file.path)
        expected = worker.delivery_root / file.scene / str(file.task_id)
        if path.parent != expected:
            raise ValueError(f'Registered delivery is not in the current declared task directory: {path}; expected={expected}')
        source, size = open_regular(path)
        with source:
            if size != file.size:
                raise ValueError(f'Registered task file size changed: {path}; registered={file.size}, actual={size}')
    destination.mkdir(parents=True, mode=0o700)
    result = {'destination': str(destination), 'complete': False, 'started_at': time.time(), 'ended_at': None,
              'members': [], 'absent_roots': [], 'metadata_only': [], 'error': None,
              'notice': 'Original rows and native file preservation only. Raw links are preserved, not followed; '
                        'no automatic extraction/restore, task resume, configuration switch, source deletion or platform send. '
                        'Keep complete original databases, config, shared materials and external service/browser data separately.'}
    original_error: BaseException | None = None
    try:
        write_json(destination / 'metadata.json', {'database': str(config.database), 'scenes': settings.scenes,
            'roots': {key: str(getattr(worker, key)) for key in ('workspace_root', 'runtime_root', 'delivery_root')},
            **metadata})
        descriptor = os.open(destination / 'native-files.tar', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            with tarfile.open(fileobj=output, mode='w', format=tarfile.PAX_FORMAT, dereference=False) as archive:
                for task in metadata['tasks']:
                    scene, id = task['scene'], str(task['id'])
                    roots = ((worker.workspace_root / scene / 'tasks' / id, f'workspaces/{scene}/tasks/{id}'),
                             (worker.runtime_root / scene / id, f'runtime/{scene}/{id}'),
                             (worker.delivery_root / scene / id, f'deliveries/{scene}/{id}'))
                    for path, member in roots:
                        require_directory(path)
                        try:
                            info = path.lstat()
                        except FileNotFoundError:
                            result['absent_roots'].append({'source': str(path), 'member': member})
                            continue
                        if not stat.S_ISDIR(info.st_mode):
                            raise ValueError(f'Task archive root must be an actual directory: {path}; mode={oct(info.st_mode)}')
                        add_node(archive, path, member, result)
            output.flush()
            os.fsync(output.fileno())
        copied = {item['name']: item for item in result['members']}
        for raw in metadata['task_files']:
            member = 'deliveries/' + Path(raw['path']).relative_to(worker.delivery_root).as_posix()
            if member not in copied or copied[member]['type'] not in {'file', 'hardlink'} or copied[member]['size'] != raw['size']:
                raise ValueError(f'Registered task copy is absent or changed in the archive: file={raw["id"]}, member={member!r}')
        result['complete'] = True
        result['ended_at'] = time.time()
    except BaseException as error:
        original_error = error
        result['error'] = ''.join(traceback.format_exception_only(error)).strip()
        error.add_note(f'Task archive is incomplete; retain and inspect {destination}; no automatic retry')
        raise
    finally:
        try:
            write_json(destination / 'result.json', result)
        except BaseException as report_error:
            if original_error is None:
                raise
            original_error.add_note(f'Task archive report also failed: {type(report_error).__name__}: {report_error}')
    return {'archive': str(destination), 'tasks': len(metadata['tasks']), 'members': len(result['members']),
            'metadata_only': len(result['metadata_only']), 'complete': result['complete']}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Task archive takes no arguments; stop the instance and configure its sole root file')
    with instance_lock(Path.cwd()):
        print(encode(archive_tasks(load_instance_config(Path.cwd()))))


if __name__ == '__main__':
    main()
