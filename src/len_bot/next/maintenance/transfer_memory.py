"""Explicit stopped-instance memory transfer; source archives never authorize runtime switching."""

from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from ..config import HostConfig, LabConfig, load_instance_config
from ..instance_lock import instance_lock
from ..models.limits import ModelBudget
from ..memory.service import LocalMemoryConfig, OpenVikingMemoryConfig, open_memory, open_memory_backend
from ..memory.jobs import FORMAT_VERSION as JOBS_FORMAT
from ..memory.local import _atomic_replace, _parts
from ..memory.openviking import _scene_path
from ..memory.role_paths import require_bot_path
from ..work.materials import open_regular
from ..models.slots import ModelSlots
from ..storage.store import FORMAT_VERSION as STORE_FORMAT, Store, encode

STRICT = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)
REASON = '显式离线跨后端记忆正文迁移'


class Document(BaseModel):
    model_config = STRICT
    scope: Literal['scene', 'public']
    scene: str
    source_path: str
    target_path: str

    def payload(self, root: Path) -> Path:
        category, qq = self.scene.split(':')
        directory = 'public' if self.scope == 'public' else f'{category}/{qq}'
        return root / 'documents' / directory / self.source_path


class Archive(BaseModel):
    model_config = STRICT
    format: Literal[1]
    exported_at: float = Field(gt=0)
    source: dict[str, JsonValue]
    target: dict[str, JsonValue]
    database: str
    scenes: list[str]
    public_scene: str
    documents: list[Document]


def backend_description(settings: LocalMemoryConfig | OpenVikingMemoryConfig) -> dict:
    if isinstance(settings, LocalMemoryConfig):
        return {'backend': 'local', 'directory': str(settings.local.directory),
                'embedding': None if settings.local.embedding is None
                else settings.local.embedding.model_dump(mode='json')}
    return {'backend': 'openviking', 'base_url': settings.openviking.base_url,
            'account_id': settings.openviking.account_id, 'public_root': settings.openviking.public_root,
            'memory_policy': None if settings.openviking.memory_policy is None else settings.openviking.memory_policy.wire(),
            'scenes': {scene: identity.user_id for scene, identity in settings.openviking.scenes.items()}}


def offline_database(path: Path, application: int, version: int) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f'Offline database must be an existing regular file: {path}')
    for suffix in ('-wal', '-journal'):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f'Stop and checkpoint before transfer; nonempty {sidecar}')
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    db.row_factory = sqlite3.Row
    actual = (db.execute('PRAGMA application_id').fetchone()[0],
              db.execute('PRAGMA user_version').fetchone()[0])
    if actual != (application, version):
        db.close()
        raise ValueError(f'Offline database needs its explicit migration: {path}; actual={actual!r}')
    return db


def selected_rows(db: sqlite3.Connection, table: str, scenes: list[str], order: str) -> list[dict]:
    rows = db.execute(f'SELECT * FROM {table} WHERE scene IN '
                      f'(SELECT value FROM json_each(?)) ORDER BY {order}', (encode(scenes),))
    return [dict(row) for row in rows]


def processing_state(jobs: sqlite3.Connection, messages: sqlite3.Connection, scenes: list[str]) -> dict:
    state = {table: selected_rows(jobs, table, scenes, order) for table, order in (
        ('memory_cursors', 'scene'), ('memory_exclusions', 'scene,message_seq'), ('memory_jobs', 'id'),
        ('memory_personas', 'scene,persona_id'))}
    maxima = {scene: messages.execute('SELECT COALESCE(MAX(seq),0) FROM messages WHERE scene=?',
                                     (scene,)).fetchone()[0] for scene in scenes}
    for row in state['memory_cursors']:
        if not 0 <= row['after_seq'] <= maxima[row['scene']]:
            raise ValueError(f'Memory cursor outside current chat range: {row!r}')
    for row in state['memory_exclusions']:
        # Retention may remove excluded originals, even without an ingest cursor.
        # Keep their exclusion metadata; any surviving original must still match.
        source = messages.execute('SELECT scene FROM messages WHERE seq=?', (row['message_seq'],)).fetchone()
        if source is not None and source['scene'] != row['scene']:
            raise ValueError(f'Memory exclusion source belongs to another scene: {row!r}')
    latest = {row['scene']: row['id'] for row in state['memory_jobs']}
    for row in state['memory_jobs']:
        details = json.loads(row['details'])
        if (row['status'] in {'queued', 'running', 'submitted'}
                or (row['id'] == latest[row['scene']] and details.get('native_phase') == 'submitting')):
            raise ValueError(f'Unfinished or unconfirmed memory extraction {row["id"]}; handle it before transfer')
    return state


def mapped_path(source_backend: str, path: str, scope: str) -> str:
    _parts(path, file=True)
    if scope == 'public':
        return path
    if source_backend == 'local':
        if path.startswith('legacy-import/'):
            raise ValueError(f'Pending legacy material must be confirmed before native migration: {path!r}')
        if path.startswith('people/'):
            parts = path.split('/')
            if len(parts) < 3 or re.fullmatch(r'[1-9][0-9]*', parts[1]) is None:
                raise ValueError(f'Local person memory lacks an actual QQ directory: {path!r}')
            target = f'peers/{parts[1]}/memories/' + '/'.join(parts[2:])
        else:
            target = 'memories/' + path
        return _scene_path(target, file=True)
    _scene_path(path, file=True)
    if path.startswith('memories/'):
        target = path.removeprefix('memories/')
    else:
        parts = path.split('/')
        target = f'people/{parts[1]}/' + '/'.join(parts[3:])
    _parts(target, file=True)
    return target


async def all_files(backend, scene: str, scope: str, path: str = '') -> list[str]:
    files = []
    offset = 0
    while True:
        page = await backend.browse(scene, path, scope=scope, offset=offset, limit=100)
        for node in page.nodes:
            if node.is_dir:
                files.extend(await all_files(backend, scene, scope, node.path))
            else:
                _parts(node.path, file=True)
                files.append(node.path)
        if not page.has_more:
            return files
        # Native pages can have no displayed items after filtering unrelated roots.
        offset += 100


def copy_regular(source: str, destination: str) -> str:
    original, _ = open_regular(Path(source))
    with original, Path(destination).open('xb') as output:
        shutil.copyfileobj(original, output, length=1024 * 1024)
    shutil.copystat(source, destination, follow_symlinks=False)
    return destination


def copy_local_source(source: Path, destination: Path) -> None:
    with closing(offline_database(source / '.memory-index.sqlite3', 0x4C424D31, 2)):
        pass
    for path in (source, *source.rglob('*')):
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f'Local memory archive requires directories and regular files: {path}; '
                             f'mode={oct(info.st_mode)}')
    shutil.copytree(source, destination, copy_function=copy_regular)


def check_archive_location(root: Path, config: HostConfig | LabConfig) -> None:
    for path in (config.database, config.database.with_name(config.database.name + '.memory.sqlite3')):
        if path.is_relative_to(root):
            raise ValueError(f'Memory archive cannot contain an active instance file: {path}')
    for settings in (config.memory, config.memory_transfer.source):
        if isinstance(settings, LocalMemoryConfig):
            directory = settings.local.directory.resolve()
            if root.is_relative_to(directory) or directory.is_relative_to(root):
                raise ValueError('Memory archive and source/target memory directories must not overlap')


def persona_ids(config: HostConfig | LabConfig) -> dict[str, set[str]]:
    scenes = config.memory_transfer.scenes
    known = {scene: set() for scene in scenes}
    path = config.database.with_name(config.database.name + '.memory.sqlite3')
    with closing(offline_database(path, 0x4C424D4A, JOBS_FORMAT)) as jobs:
        for row in selected_rows(jobs, 'memory_personas', scenes, 'scene,persona_id'):
            known[row['scene']].add(row['persona_id'])
    return known


def require_role_mapping(config: HostConfig | LabConfig, item: Document, known: dict[str, set[str]]) -> None:
    if item.scope == 'scene':
        require_bot_path(item.source_path, known[item.scene], native=config.memory_transfer.source.backend == 'openviking')
        require_bot_path(item.target_path, known[item.scene], native=config.memory.backend == 'openviking')


def read_original(path: Path) -> bytes:
    source, _ = open_regular(path)
    with source:
        return source.read()


async def export_archive(config: HostConfig | LabConfig) -> dict:
    settings = config.memory_transfer
    root = settings.archive
    check_archive_location(root, config)
    known = persona_ids(config)
    jobs_path = config.database.with_name(config.database.name + '.memory.sqlite3')
    with closing(offline_database(config.database, 0x4C424E31, STORE_FORMAT)) as messages, \
            closing(offline_database(jobs_path, 0x4C424D4A, JOBS_FORMAT)) as jobs:
        processing_state(jobs, messages, settings.scenes)
    root.mkdir(mode=0o700)
    report = {'operation': 'export', 'started': time.time(), 'finished': None,
              'documents': [], 'error': None, 'notice':
              'Current Markdown bodies only. Native service history remains at the source; '
              'chat and processing snapshots are retained. No source deletion or runtime switch.'}
    _atomic_replace(root / 'export-result.json', encode(report))
    original_error: BaseException | None = None
    try:
        shutil.copy2(config.database, root / 'chat.sqlite3')
        shutil.copy2(jobs_path, root / 'memory-jobs.sqlite3')
        source = settings.source
        if isinstance(source, LocalMemoryConfig):
            copy_local_source(source.local.directory, root / 'local-source')
            source = source.model_copy(update={'local': source.local.model_copy(
                update={'directory': root / 'local-source'})})
        source_config = config.model_copy(update={'memory': source, 'replay_memory': None})
        documents = []
        occupied = set()
        async with open_memory_backend(source_config) as backend:
            partitions = [('scene', scene) for scene in settings.scenes]
            if (isinstance(source, LocalMemoryConfig) or source.openviking.public_root is not None):
                partitions.append(('public', settings.public_scene))
            for scope, scene in partitions:
                for path in await all_files(backend, scene, scope):
                    target = mapped_path(source.backend, path, scope)
                    key = (scope, scene if scope == 'scene' else '', target)
                    if key in occupied:
                        raise ValueError(f'Source paths collide in target memory: {key!r}')
                    occupied.add(key)
                    item = Document(scope=scope, scene=scene, source_path=path, target_path=target)
                    require_role_mapping(config, item, known)
                    content = (await backend.read(scene, path, scope=scope)).content
                    payload = item.payload(root)
                    payload.parent.mkdir(parents=True, exist_ok=True)
                    with payload.open('xb') as stream:
                        stream.write(content.encode('utf-8'))
                    documents.append(item)
                    report['documents'].append(item.model_dump())
                    _atomic_replace(root / 'export-result.json', encode(report))
        archive = Archive(format=1, exported_at=time.time(), source=backend_description(settings.source),
                          target=backend_description(config.memory), database=str(config.database),
                          scenes=settings.scenes, public_scene=settings.public_scene, documents=documents)
        with (root / 'manifest.json').open('x', encoding='utf-8') as stream:
            stream.write(archive.model_dump_json(indent=2))
        report['finished'] = time.time()
    except BaseException as error:
        original_error = error
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        try:
            _atomic_replace(root / 'export-result.json', encode(report))
        except Exception as report_error:
            if original_error is None:
                raise
            original_error.add_note(f'Memory export report also failed: {type(report_error).__name__}: {report_error}')
    return {'archive': str(root), 'documents': len(documents), 'report': report}


def load_archive(config: HostConfig | LabConfig) -> tuple[Archive, list[tuple[Document, str]]]:
    settings = config.memory_transfer
    root = settings.archive
    check_archive_location(root, config)
    raw = read_original(root / 'manifest.json')
    try:
        archive = Archive.model_validate_json(raw)
    except ValidationError as error:
        raise ValueError(f'Invalid memory transfer manifest: {error}; raw={raw[:1000]!r}') from error
    if (archive.source != backend_description(settings.source) or
            archive.target != backend_description(config.memory) or archive.database != str(config.database)
            or archive.scenes != settings.scenes or archive.public_scene != settings.public_scene):
        raise ValueError('Archive source/target identities, database or selected scenes differ from root configuration')
    contents = []
    occupied = set()
    known = persona_ids(config)
    for item in archive.documents:
        if (item.scene not in settings.scenes or
                (item.scope == 'public' and item.scene != settings.public_scene)):
            raise ValueError(f'Archive document is outside its source partition: {item!r}')
        if item.target_path != mapped_path(settings.source.backend, item.source_path, item.scope):
            raise ValueError(f'Archive target path differs from explicit backend mapping: {item!r}')
        require_role_mapping(config, item, known)
        key = (item.scope, item.scene if item.scope == 'scene' else '', item.target_path)
        if key in occupied:
            raise ValueError(f'Duplicate archive target: {key!r}')
        occupied.add(key)
        path = item.payload(root)
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f'Memory payload escapes its archive: {path}')
        data = read_original(path)
        try:
            contents.append((item, data.decode('utf-8')))
        except UnicodeDecodeError as error:
            raise ValueError(f'Invalid UTF-8 memory payload {path}; raw={data[error.start:error.end + 30]!r}') from error
    return archive, contents


def check_processing_archive(config: HostConfig | LabConfig) -> None:
    settings = config.memory_transfer
    jobs_path = config.database.with_name(config.database.name + '.memory.sqlite3')
    with closing(offline_database(config.database, 0x4C424E31, STORE_FORMAT)) as messages, \
            closing(offline_database(jobs_path, 0x4C424D4A, JOBS_FORMAT)) as jobs, \
            closing(offline_database(settings.archive / 'chat.sqlite3', 0x4C424E31, STORE_FORMAT)) as old_messages, \
            closing(offline_database(settings.archive / 'memory-jobs.sqlite3', 0x4C424D4A, JOBS_FORMAT)) as old_jobs:
        if processing_state(jobs, messages, settings.scenes) != processing_state(old_jobs, old_messages, settings.scenes):
            raise ValueError('Actual memory cursor, exclusions or extraction results changed since export')
        if selected_rows(messages, 'messages', settings.scenes, 'seq') != selected_rows(
                old_messages, 'messages', settings.scenes, 'seq'):
            raise ValueError('Actual selected-scene chat records changed since export')


async def import_archive(config: HostConfig | LabConfig) -> dict:
    settings = config.memory_transfer
    archive, contents = load_archive(config)
    check_processing_archive(config)
    report_path = settings.archive / 'import-result.json'
    if report_path.exists():
        raise FileExistsError(f'Transfer already attempted; inspect actual target and report: {report_path}')
    if isinstance(config.memory, LocalMemoryConfig):
        directory = config.memory.local.directory
        if directory.exists() and any(directory.iterdir()):
            raise ValueError(f'Local target must be an entirely new empty directory: {directory}')
    elif any(item.scope == 'public' for item, _ in contents) and config.memory.openviking.public_root is None:
        raise ValueError('Public archive content requires an explicit target public_root; it is not scene memory')
    report = {'operation': 'import', 'started': time.time(), 'finished': None,
              'writes': [], 'resumed_jobs': [], 'error': None, 'not_transferred':
              ['Source revision history stays in its archive/service', 'Derived summaries and vectors are not copied',
               'Original chat and processing database remain in place; no backend configuration is rewritten']}
    with report_path.open('x', encoding='utf-8') as stream:
        stream.write(encode(report))
    original_error: BaseException | None = None
    try:
        # Disable only derived summary generation in this offline writer, never runtime settings.
        writer_config = config.model_copy(update={'memory': config.memory.model_copy(update={'summaries': False})})
        slots = ModelSlots(config.max_model_requests)
        with Store(config.database) as store:
            async with open_memory(writer_config, store, slots=slots) as service:
                budget = ModelBudget(config, store, service, root=config._instance_root)
                slots.admit = budget.check
                if isinstance(config.memory, OpenVikingMemoryConfig):
                    partitions = [('scene', scene) for scene in archive.scenes]
                    if config.memory.openviking.public_root is not None:
                        partitions.append(('public', settings.public_scene))
                    for scope, scene in partitions:
                        if await all_files(service.backend, scene, scope):
                            raise ValueError(f'Native target partition already has current content: {scope}, {scene}')
                for item, content in contents:
                    write = {**item.model_dump(), 'started': time.time(), 'finished': None, 'result': None}
                    report['writes'].append(write)
                    _atomic_replace(report_path, encode(report))
                    if isinstance(config.memory, LocalMemoryConfig):
                        result = await service.write(item.scene, item.target_path, content, REASON, scope=item.scope)
                    else:
                        result = asdict(await service.backend.write(item.scene, item.target_path, content, scope=item.scope))
                    write.update(finished=time.time(), result=result)
                    _atomic_replace(report_path, encode(report))
                if settings.resume_failed:
                    for scene in archive.scenes:
                        previous = service.jobs.latest(scene)
                        if previous is None or previous['status'] not in {'failed', 'interrupted'}:
                            continue
                        resumed = service.jobs.create(scene, 'local', previous['first_seq'], previous['through_seq'])
                        resumed['details'].update(retry_of=previous['id'], transfer_archive=str(settings.archive))
                        service.jobs.details(resumed)
                        report['resumed_jobs'].append({'scene': scene, 'job': resumed['id'],
                                                       'source_job': previous['id'],
                                                       'first_seq': resumed['first_seq'],
                                                       'through_seq': resumed['through_seq']})
                    _atomic_replace(report_path, encode(report))
        report['finished'] = time.time()
    except BaseException as error:
        original_error = error
        report['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        try:
            _atomic_replace(report_path, encode(report))
        except Exception as report_error:
            if original_error is None:
                raise
            original_error.add_note(f'Memory import report also failed: {type(report_error).__name__}: {report_error}')
    return {'archive': str(settings.archive), 'report': str(report_path), 'result': report}


async def transfer(config: HostConfig | LabConfig) -> dict:
    if config.memory_transfer is None:
        raise ValueError('Configure memory_transfer explicitly in the instance root lenbot.config.json')
    if config.replay_memory is not None or (isinstance(config, LabConfig) and config.replay_clock is not None):
        raise ValueError('Memory transfer is not a recorded or fixed-clock replay operation')
    if config.memory_transfer.operation == 'export':
        return await export_archive(config)
    return await import_archive(config)


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Memory transfer takes no overrides; stop the instance and configure its root file')
    with instance_lock(Path.cwd()):
        print(encode(asyncio.run(transfer(load_instance_config(Path.cwd())))))


if __name__ == '__main__':
    main()
