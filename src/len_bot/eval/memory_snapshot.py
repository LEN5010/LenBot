"""Offline local-memory seeds; preserve content, index and extraction cursor together."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field
import filecmp
import json
from pathlib import Path
import shutil
import sqlite3

from .cases import InitialMemory
from ..next.config import LabConfig
from ..next.memory.service import LocalMemoryConfig
from ..next.memory.jobs import FORMAT_VERSION


def offline_database(path: Path, application: int, version: int) -> sqlite3.Connection:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Memory snapshot requires a regular database: {path}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"Memory snapshot must be stopped without nonempty {suffix}: {path}")
    db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        if (db.execute('PRAGMA application_id').fetchone()[0] != application
                or db.execute('PRAGMA user_version').fetchone()[0] != version):
            raise ValueError(f"Memory snapshot requires application {application} format {version}: {path}")
        return db
    except BaseException:
        db.close()
        raise


def tree_files(root: Path) -> dict[str, Path]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f"Memory snapshot directory is missing or is a symlink: {root}")
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError(f"Memory snapshot contains a non-regular path: {path}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path
    return result


def check_memory(source: InitialMemory, config: LabConfig, history: Path) -> None:
    if not isinstance(config.memory, LocalMemoryConfig):
        raise ValueError('initial_memory requires the configured local memory backend')
    files = tree_files(source.directory)
    allowed = {config.scene, 'public'}
    kind, qq = config.scene.split(':')
    category = 'groups' if kind == 'group' else 'private'
    # Check files as well as index rows: unindexed text is still real source data.
    for name in files:
        parts = Path(name).parts
        if parts[0] in {'groups', 'private'} and (len(parts) < 3 or parts[:2] != (category, qq)):
            raise ValueError(f"Memory snapshot contains a different scene's files: {name}")
    with closing(offline_database(source.directory / '.memory-index.sqlite3', 0x4C424D31, 2)) as db:
        scopes = {row[0] for row in db.execute(
            'SELECT scope FROM memory_files UNION SELECT scope FROM memory_changes')}
        if scopes - allowed:
            raise ValueError(f'Memory index contains other scenes: {sorted(scopes - allowed)}')
        row = db.execute('SELECT provider,base_url,model,dimensions FROM memory_vector_binding WHERE id=1').fetchone()
        embedding = config.memory.local.embedding
        vectors = db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_vec'").fetchone() is not None
        if row is not None:
            if embedding is None:
                raise ValueError('Seed has vectors but root configuration selects FTS-only; no automatic rebuild')
            expected = (embedding.provider, config.models.providers[embedding.provider].base_url, embedding.model)
            if row[:3] != expected or (embedding.dimensions is not None and row[3] != embedding.dimensions):
                raise ValueError('Seed vector binding differs from configured memory model')
            if not vectors:
                raise ValueError('Seed vector binding has no vector table; rebuild explicitly offline')
        elif embedding is not None and db.execute('SELECT 1 FROM memory_files LIMIT 1').fetchone() is not None:
            raise ValueError('Seed contains memories without configured vectors; rebuild explicitly while offline')
        elif vectors:
            raise ValueError('Seed vector table has no binding; rebuild explicitly offline')
    with closing(offline_database(source.jobs, 0x4C424D4A, FORMAT_VERSION)) as jobs:
        scopes = {row[0] for row in jobs.execute(' UNION '.join(
            f'SELECT scene FROM {name}' for name in ('memory_cursors', 'memory_jobs', 'memory_exclusions',
                                                     'memory_summary_runs', 'memory_embedding_calls', 'memory_personas')))}
        scopes.update(row[0] for row in jobs.execute('SELECT scope FROM memory_summary_runs'))
        if scopes - allowed:
            raise ValueError(f'Memory processing seed contains other scenes: {sorted(scopes - allowed)}')
        if jobs.execute("SELECT 1 FROM memory_jobs WHERE backend!='local' AND status!='complete' LIMIT 1").fetchone():
            raise ValueError('Seed contains unfinished remote memory work')
        with closing(sqlite3.connect(history.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
            maximum = db.execute('SELECT COALESCE(MAX(seq),0) FROM messages WHERE scene=?', (config.scene,)).fetchone()[0]
        for table, column in (('memory_cursors', 'after_seq'), ('memory_exclusions', 'message_seq'),
                              ('memory_jobs', 'through_seq')):
            if jobs.execute(f'SELECT 1 FROM {table} WHERE scene=? AND {column}>? LIMIT 1',
                            (config.scene, maximum)).fetchone():
                raise ValueError(f'{table} refers beyond the supplied initial chat history')


def freeze_memory(source: InitialMemory, destination: Path) -> None:
    if destination.is_relative_to(source.directory):
        raise ValueError('Run archive cannot be inside the source memory directory')
    destination.mkdir()
    shutil.copytree(source.directory, destination / 'memory', copy_function=shutil.copy2)
    shutil.copy2(source.jobs, destination / 'jobs.sqlite3')


@dataclass
class MemoryBaseline:
    extraction_calls: dict[int, int] = field(default_factory=dict)
    extraction_errors: dict[int, str | None] = field(default_factory=dict)
    summaries: int = 0
    embeddings: int = 0


def install_memory(archive: Path, repeat: Path) -> MemoryBaseline:
    shutil.copytree(archive / 'memory', repeat / 'memory', copy_function=shutil.copy2)
    shutil.copy2(archive / 'jobs.sqlite3', repeat / 'chat.sqlite3.memory.sqlite3')
    with closing(sqlite3.connect((archive / 'jobs.sqlite3').as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        return MemoryBaseline(
            extraction_calls={id: len(json.loads(details)['calls']) for id, details in
                              db.execute("SELECT id,details FROM memory_jobs WHERE backend='local'")},
            extraction_errors=dict(db.execute("SELECT id,error FROM memory_jobs WHERE backend='local'")),
            summaries=db.execute('SELECT COALESCE(MAX(id),0) FROM memory_summary_runs').fetchone()[0],
            embeddings=db.execute('SELECT COALESCE(MAX(id),0) FROM memory_embedding_calls').fetchone()[0])


def observed_memory(path: Path, baseline: MemoryBaseline) -> tuple[list[dict], list[dict]]:
    """Include new calls appended to an existing seed job, not just new job rows."""
    calls, errors = [], []
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        for row in db.execute("SELECT * FROM memory_jobs WHERE backend='local' ORDER BY id"):
            prior = baseline.extraction_calls.get(row['id'], 0)
            exchanges = json.loads(row['details'])['calls']
            for index, call in enumerate(exchanges[prior:], prior):
                calls.append({'source': 'memory_extract', 'job_id': row['id'], 'call_index': index,
                              'scene': row['scene'], 'started': call['started'],
                              'ended': call.get('ended'), 'usage': call.get('usage'),
                              'tokens': call.get('tokens'), 'error': call.get('error')})
            if row['error'] is not None and (
                    row['id'] not in baseline.extraction_calls or len(exchanges) > prior
                    or row['error'] != baseline.extraction_errors[row['id']]):
                errors.append({'job_id': row['id'], 'error': row['error']})
        for table, after, source, start in (
            ('memory_summary_runs', baseline.summaries, 'memory_summary', 'model_started'),
            ('memory_embedding_calls', baseline.embeddings, 'memory_embedding', 'started'),
        ):
            for row in db.execute(f'SELECT * FROM {table} WHERE id>? ORDER BY id', (after,)):
                if row['error'] is not None:
                    errors.append({'source': source, 'id': row['id'], 'error': row['error']})
                if row[start] is None:
                    continue
                calls.append({'source': source, 'id': row['id'], 'scene': row['scene'],
                              'started': row[start], 'ended': row['ended'],
                              'usage': None if row['usage'] is None else json.loads(row['usage']),
                              'tokens': None if row['tokens'] is None else json.loads(row['tokens']),
                              'error': row['error']})
    return calls, errors


def same_memory(left: Path, right: Path) -> bool:
    for root in (left, right):
        for name in ('jobs.sqlite3', 'memory/.memory-index.sqlite3'):
            if not (root / name).is_file():
                raise ValueError(f'Memory seed archive is incomplete: {root / name}')
    a, b = tree_files(left), tree_files(right)
    if a.keys() != b.keys():
        return False
    for name, source in a.items():
        if not filecmp.cmp(source, b[name], shallow=False):
            return False
        if name.endswith('.md') and source.stat().st_mtime_ns != b[name].stat().st_mtime_ns:
            return False
    return True
