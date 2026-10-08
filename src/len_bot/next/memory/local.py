"""File-authoritative scene memory with explicit text or hybrid retrieval.

Embeddings are optional and separately configured; this module does not
extract facts from a conversation.
Manual text searches match a literal phrase; automatic text recall ranks
overlapping literal trigrams from the recent chat, or a shorter literal query.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import sys
from urllib.parse import quote, unquote
import tempfile
import time
import threading
from collections.abc import Callable
from contextlib import AsyncExitStack, closing, contextmanager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from .embeddings import EmbeddingBinding, EmbeddingClient
from .types import MemoryDocument, MemoryNode, MemoryPage
from ..storage.sqlite import connect
from ..runtime.logs import log_event


_SCENE = re.compile(r"[a-z][a-z0-9_-]*:(group|private):[^:\s/\\]+\Z")
_INDEX_NAME = ".memory-index.sqlite3"
# Derived directory summaries: L0 abstract and L1 overview, never indexed content.
SUMMARY_FILES = (".abstract.md", ".overview.md")
ABSTRACT_CHARS = 256
OVERVIEW_CHARS = 4000
PENDING_PREFIX = "legacy-import/"
_APPLICATION_ID = 0x4C424D31
FORMAT_VERSION = 3
SUMMARY_SCHEMA = """
CREATE TABLE memory_summaries (
    scope TEXT NOT NULL,
    path TEXT NOT NULL,
    generated_at REAL NOT NULL,
    PRIMARY KEY(scope, path)
);
"""
_Result = TypeVar("_Result")
_FILE_LOCK = threading.RLock()
_PENDING_FILE = '.memory-pending.json'
logger = logging.getLogger(__name__)


class VectorIndexNeedsRebuild(ValueError):
    """The configured embedding cannot query the existing derived index."""


class _FileMutation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    scope: str
    path: str
    action: Literal['write', 'delete', 'forget', 'summary', 'clear-summary']
    changed_at: float
    files: dict[str, str | None]


def _sync_directory(path: Path) -> None:
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _recover_files(root: Path, base: Path) -> None:
    """Roll back only file replacements whose SQLite transaction did not commit."""
    with _FILE_LOCK:
        pending = base / _PENDING_FILE
        if not pending.exists():
            return
        change = _FileMutation.model_validate_json(pending.read_bytes())
        with closing(connect(root / _INDEX_NAME, readonly=True)) as db:
            if change.action in {'write', 'delete'}:
                committed = db.execute('SELECT 1 FROM memory_changes WHERE scope=? AND path=? '
                                       'AND changed_at=? AND action=?',
                                       (change.scope, change.path, change.changed_at, change.action)).fetchone() is not None
            elif change.action == 'forget':
                committed = db.execute('SELECT 1 FROM memory_files WHERE scope=? AND path=?',
                                       (change.scope, change.path)).fetchone() is None
            else:
                row = db.execute('SELECT generated_at FROM memory_summaries WHERE scope=? AND path=?',
                                 (change.scope, change.path)).fetchone()
                committed = row is None if change.action == 'clear-summary' else row is not None and row[0] == change.changed_at
        if not committed:
            for relative, content in change.files.items():
                target = base / relative
                if not target.resolve().is_relative_to(base.resolve()):
                    raise ValueError(f'Memory replacement escapes its partition: {relative!r}')
                if content is None:
                    target.unlink(missing_ok=True)
                    if target.parent.exists():
                        _sync_directory(target.parent)
                else:
                    _atomic_replace(target, content)
        pending.unlink()
        _sync_directory(base)


async def _finish_write_thread(operation: Callable[..., _Result], *args: object) -> _Result:
    """Keep the caller's scope lock until an already-started write really ends."""
    thread = asyncio.create_task(asyncio.to_thread(operation, *args))
    try:
        return await asyncio.shield(thread)
    except asyncio.CancelledError as cancelled:
        # Cancelling an await cannot stop its thread. Even another cancel must
        # not release the lock while the Markdown/SQLite mutation continues.
        while not thread.done():
            try:
                await asyncio.shield(thread)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        try:
            thread.result()
        except BaseException as error:
            cancelled.add_note(f"Local memory write also failed: {type(error).__name__}: {error}")
            raise cancelled from error
        raise


class LocalMemorySettings(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    directory: Path
    embedding: EmbeddingBinding | None = None


@dataclass(frozen=True, slots=True)
class LocalMemoryHit:
    scope: Literal["scene", "public"]
    path: str
    preview: str
    total_chars: int


@dataclass(frozen=True, slots=True)
class LocalMemoryChange:
    action: Literal["write", "delete"]
    path: str
    changed_at: float
    reason: str
    before: str | None
    after: str | None


@dataclass(frozen=True, slots=True)
class LocalMemoryForget:
    path: str
    removed_current: bool
    removed_history_versions: int
    removed_summaries: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LocalMemorySummary:
    path: str
    abstract: str | None
    overview: str | None
    generated_at: float | None
    # Latest recorded change below this directory after the summary (or with none).
    changed_after: float | None


def _scene_scope(scene: str) -> str:
    match = _SCENE.fullmatch(scene)
    if match is None:
        raise ValueError(f"scene must be an existing platform:group:id or platform:private:id identity: {scene!r}")
    return scene


def scope_directory(scene: str) -> str:
    return quote(scene, safe='') if sys.platform == 'win32' else scene


def _parts(path: str, *, file: bool) -> tuple[str, ...]:
    if not path:
        if file:
            raise ValueError("memory file path is required")
        return ()
    if path.startswith("/") or path.endswith("/"):
        raise ValueError(f"memory path must be relative: {path!r}")
    parts = tuple(path.split("/"))
    if any(not part or part in {".", ".."} or part in SUMMARY_FILES
           or any(ord(character) < 32 for character in part)
           or any(character in part for character in "\\?#%") for part in parts):
        raise ValueError(f"memory path has an unsupported segment: {path!r}")
    if file and not parts[-1].endswith(".md"):
        raise ValueError(f"memory file must be Markdown: {path!r}")
    return parts


def _source_text(path: Path) -> str:
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as error:
        snippet = data[max(0, error.start - 30):error.end + 30]
        raise ValueError(f"{path}: invalid UTF-8: {error}; raw={snippet!r}") from error


def _atomic_replace(path: Path, content: str, *, create_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".memory-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        if create_only:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _summary_times(db: sqlite3.Connection, scope: str, path: str) -> tuple[float | None, float | None]:
    row = db.execute("SELECT generated_at FROM memory_summaries WHERE scope=? AND path=?", (scope, path)).fetchone()
    generated_at = None if row is None else row[0]
    prefix = f"{path}/" if path else ""
    latest = db.execute("SELECT MAX(changed_at) FROM memory_changes WHERE scope=? AND substr(path,1,?)=?",
                        (scope, len(prefix), prefix)).fetchone()[0]
    changed_after = latest if latest is not None and (generated_at is None or latest > generated_at) else None
    return generated_at, changed_after


def scene_overview(root: Path, scene: str) -> str | None:
    with _FILE_LOCK:
        _recover_files(root, root / 'scenes' / scope_directory(scene))
        return _scene_overview(root, scene)


def _scene_overview(root: Path, scene: str) -> str | None:
    """Read a scene root overview only when the recorded source changes have not invalidated it."""
    _scene_scope(scene)
    base = root.expanduser().resolve() / "scenes" / scope_directory(scene)
    files = [base / name for name in SUMMARY_FILES]
    for path in (base, *files):
        if path.is_symlink():
            raise ValueError(f"memory summary path is a symlink: {path}")
    present = [file.exists() for file in files]
    if not any(present):
        return None
    if not all(present):
        raise ValueError(f"incomplete memory summary files in {base}: {dict(zip(SUMMARY_FILES, present))}")
    index = root.expanduser().resolve() / _INDEX_NAME
    with closing(connect(index, readonly=True)) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != FORMAT_VERSION:
            raise ValueError(f"local memory index format {version} requires offline migration: {index}; "
                             "run python -m len_bot.next.maintenance.migrate_local_memory")
        generated_at, changed_after = _summary_times(db, scene, "")
    if generated_at is None:
        raise ValueError(f"memory summary has no generation time: {base}")
    if changed_after is not None:
        return None
    return _source_text(files[1])


class LocalMemory:
    def __init__(self, settings: LocalMemorySettings, *, embedding: EmbeddingClient | None = None):
        if (settings.embedding is None) != (embedding is None):
            raise ValueError("local memory embedding binding and client must be configured together")
        if embedding is not None and (
            embedding.settings.provider != settings.embedding.provider
            or embedding.settings.model != settings.embedding.model
            or embedding.settings.dimensions != settings.embedding.dimensions
        ):
            raise ValueError("local memory embedding client does not match configured provider/model/dimensions")
        self.settings = settings
        self.embedding = embedding
        self.track_embedding = None
        self.root = settings.directory.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = self.root / _INDEX_NAME
        if self.index.is_symlink():
            raise ValueError(f"memory index must not be a symlink: {self.index}")
        self._locks: dict[str, asyncio.Lock] = {}
        self._initialize()
        for base in (self.root / 'public', *(self.root / 'scenes').glob('*')):
            _recover_files(self.root, base)

    @asynccontextmanager
    async def _lock(self, scope: str):
        async with self._locks.setdefault(scope, asyncio.Lock()):
            await asyncio.to_thread(_recover_files, self.root, self._base(scope))
            yield

    @contextmanager
    def _file_mutation(self, scope: str, path: str, action: str, changed_at: float, targets: list[Path]):
        base = self._base(scope)
        base.mkdir(parents=True, exist_ok=True)
        pending = base / _PENDING_FILE
        with _FILE_LOCK:
            _recover_files(self.root, base)
            change = _FileMutation(scope=scope, path=path, action=action, changed_at=changed_at,
                                   files={str(target.relative_to(base)): _source_text(target) if target.exists() else None
                                          for target in targets})
            with self._db() as db:
                db.execute('BEGIN IMMEDIATE')
                _atomic_replace(pending, change.model_dump_json())
                yield db
                for target in targets:
                    if target.parent.exists():
                        _sync_directory(target.parent)
            pending.unlink()
            _sync_directory(base)

    def _connect(self) -> sqlite3.Connection:
        db = connect(self.index)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        if self.embedding is not None:
            self._load_vec(db)
        return db

    @staticmethod
    def _load_vec(db: sqlite3.Connection) -> None:
        import sqlite_vec

        db.enable_load_extension(True)
        try:
            sqlite_vec.load(db)
        finally:
            db.enable_load_extension(False)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self._db() as db:
            identity = (db.execute("PRAGMA application_id").fetchone()[0],
                        db.execute("PRAGMA user_version").fetchone()[0])
            if identity == (_APPLICATION_ID, FORMAT_VERSION):
                return
            if identity == (_APPLICATION_ID, 2):
                raise ValueError(f"local memory index format 2 requires offline migration: {self.index}; "
                                 "run python -m len_bot.next.maintenance.migrate_local_memory")
            if identity != (0, 0) or db.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is not None:
                raise ValueError(f"unsupported local memory index format at {self.index}: "
                                 f"application_id={identity[0]}, user_version={identity[1]}")
            # One transaction: an interrupted first start leaves an empty file, not half a schema.
            db.executescript(f"""
                    BEGIN IMMEDIATE;
                    CREATE TABLE memory_files (
                        id INTEGER PRIMARY KEY,
                        scope TEXT NOT NULL,
                        path TEXT NOT NULL,
                        content TEXT NOT NULL,
                        UNIQUE(scope, path)
                    );
                    CREATE VIRTUAL TABLE memory_fts USING fts5(
                        content, content='memory_files', content_rowid='id',
                        tokenize='trigram case_sensitive 1'
                    );
                    INSERT INTO memory_fts(memory_fts, rank) VALUES('secure-delete', 1);
                    CREATE TABLE memory_changes (
                        id INTEGER PRIMARY KEY,
                        scope TEXT NOT NULL,
                        path TEXT NOT NULL,
                        changed_at REAL NOT NULL,
                        action TEXT NOT NULL CHECK(action IN ('write', 'delete')),
                        reason TEXT NOT NULL,
                        before TEXT,
                        after TEXT
                    );
                    CREATE INDEX memory_changes_path ON memory_changes(scope, path, id);
                    CREATE TABLE memory_vector_binding (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        provider TEXT NOT NULL,
                        base_url TEXT NOT NULL,
                        model TEXT NOT NULL,
                        dimensions INTEGER NOT NULL CHECK(dimensions > 0)
                    );
                    {SUMMARY_SCHEMA}
                    PRAGMA application_id={_APPLICATION_ID};
                    PRAGMA user_version={FORMAT_VERSION};
                    COMMIT;
                """)

    def _base(self, scope: str) -> Path:
        if scope == "public":
            return self.root / "public"
        match = _SCENE.fullmatch(scope)
        if match is None:
            raise ValueError(f"invalid memory source scope: {scope!r}")
        return self.root / "scenes" / scope_directory(scope)

    @staticmethod
    def _source(scene: str, scope: Literal["scene", "public"]) -> str:
        current = _scene_scope(scene)
        if scope == "scene":
            return current
        if scope == "public":
            return "public"
        raise ValueError(f"unknown memory read scope: {scope!r}")

    def _target(self, scope: str, path: str, *, file: bool) -> Path:
        parts = _parts(path, file=file)
        base = self._base(scope)
        target = base.joinpath(*(quote(part, safe='') for part in parts)) if sys.platform == 'win32' else base.joinpath(*parts)
        parent = base
        for part in target.relative_to(base).parts:
            if parent.exists() and (parent / part).exists():
                differently_cased = next((child.name for child in parent.iterdir()
                                         if child.name != part and child.name.casefold() == part.casefold()), None)
                if differently_cased is not None:
                    raise ValueError(f'Memory path casing differs from existing {differently_cased!r}: {path!r}')
            parent /= part
        for ancestor in (base, *target.parents):
            if ancestor == self.root:
                break
            if ancestor.is_symlink():
                raise ValueError(f"memory path crosses a symlink: {ancestor}")
        if target.is_symlink():
            raise ValueError(f"memory path is a symlink: {target}")
        if not target.resolve(strict=False).is_relative_to(base.resolve(strict=False)):
            raise ValueError(f"memory path escapes its source scope: {path!r}")
        return target

    def _source_files(self) -> tuple[dict[tuple[str, str], str], list[Path]]:
        files: dict[tuple[str, str], str] = {}
        summaries: list[Path] = []
        for category in ("public", "scenes"):
            directory = self.root / category
            if not directory.exists():
                continue
            if directory.is_symlink():
                raise ValueError(f"memory source root is a symlink: {directory}")
            for path in directory.rglob("*.md"):
                if path.name in SUMMARY_FILES:
                    summaries.append(path)
                    continue
                if category == "public":
                    scope, relative = "public", path.relative_to(directory).as_posix()
                    if sys.platform == 'win32':
                        relative = '/'.join(unquote(part) for part in path.relative_to(directory).parts)
                else:
                    position = path.relative_to(directory).parts
                    disk_scope = unquote(position[0]) if sys.platform == 'win32' else position[0]
                    if len(position) < 2 or _SCENE.fullmatch(disk_scope) is None:
                        raise ValueError(f"unexpected memory source path: {path}")
                    scope = disk_scope
                    relative = '/'.join(unquote(part) for part in position[1:]) if sys.platform == 'win32' else Path(*position[1:]).as_posix()
                actual = self._target(scope, relative, file=True)
                files[(scope, relative)] = _source_text(actual)
        return files, summaries

    def reindex(self) -> int:
        """Offline text rebuild; clear derived summaries and any old vector index."""
        if self.embedding is not None:
            raise ValueError("configured embedding requires await reindex_embeddings() offline")
        files, summaries = self._source_files()
        with self._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_vec'").fetchone() is not None:
                self._load_vec(db)
            db.execute("BEGIN EXCLUSIVE")
            for summary in summaries:
                summary.unlink()
            db.execute("DELETE FROM memory_summaries")
            if self._vector_table_exists(db):
                db.execute("DROP TABLE memory_vec")
            db.execute("DELETE FROM memory_vector_binding")
            old_rows = db.execute("SELECT id, content FROM memory_files").fetchall()
            for row in old_rows:
                self._remove_index(db, row)
            for (scope, path), content in sorted(files.items()):
                self._put_index(db, scope, path, content, None)
        return len(files)

    @staticmethod
    def _indexed(db: sqlite3.Connection, scope: str, path: str, current: str | None) -> sqlite3.Row | None:
        row = db.execute("SELECT id, content FROM memory_files WHERE scope=? AND path=?", (scope, path)).fetchone()
        if (row is None) != (current is None) or (row is not None and row["content"] != current.casefold()):
            raise ValueError(f"local memory Markdown/index mismatch at {scope}/{path}; rebuild offline")
        return row

    @staticmethod
    def _put_index(db: sqlite3.Connection, scope: str, path: str, text: str,
                   old: sqlite3.Row | None) -> None:
        folded = text.casefold()
        if old is None:
            cursor = db.execute("INSERT INTO memory_files(scope, path, content) VALUES(?,?,?)",
                                (scope, path, folded))
            db.execute("INSERT INTO memory_fts(rowid, content) VALUES(?,?)", (cursor.lastrowid, folded))
        else:
            db.execute("INSERT INTO memory_fts(memory_fts, rowid, content) VALUES('delete', ?, ?)",
                       (old["id"], old["content"]))
            db.execute("UPDATE memory_files SET content=? WHERE id=?", (folded, old["id"]))
            db.execute("INSERT INTO memory_fts(rowid, content) VALUES(?,?)", (old["id"], folded))

    @staticmethod
    def _remove_index(db: sqlite3.Connection, old: sqlite3.Row | None) -> None:
        if old is not None:
            db.execute("INSERT INTO memory_fts(memory_fts, rowid, content) VALUES('delete', ?, ?)",
                       (old["id"], old["content"]))
            db.execute("DELETE FROM memory_files WHERE id=?", (old["id"],))

    @staticmethod
    def _vector_table_exists(db: sqlite3.Connection) -> bool:
        return db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_vec'").fetchone() is not None

    def _mutation_preflight(self, db: sqlite3.Connection) -> None:
        if self.embedding is None and self._vector_table_exists(db):
            raise ValueError("FTS-only mutation requires offline reindex() to remove the old vector index")

    def _vector_binding(self, db: sqlite3.Connection) -> int | None:
        """Reject a changed model binding; text/vector rows are updated together."""
        if self.embedding is None:
            return None
        row = db.execute("SELECT provider,base_url,model,dimensions FROM memory_vector_binding WHERE id=1").fetchone()
        if row is None:
            if db.execute("SELECT 1 FROM memory_files LIMIT 1").fetchone() is not None:
                raise VectorIndexNeedsRebuild("configured embedding has existing Markdown without vectors; "
                                 "停机后在实例目录执行 python -m len_bot.next.maintenance.memory_reindex")
            return None
        settings = self.embedding.settings
        if (row["base_url"] != settings.base_url
                or row["model"] != settings.model
                or (settings.dimensions is not None and row["dimensions"] != settings.dimensions)):
            raise VectorIndexNeedsRebuild("记忆向量索引与当前地址/模型/维数不一致；"
                             f"索引：{row['base_url']} / {row['model']} / {row['dimensions']}；"
                             f"配置：{settings.base_url} / {settings.model} / {settings.dimensions}。"
                             "停机后在实例目录执行 python -m len_bot.next.maintenance.memory_reindex")
        if not self._vector_table_exists(db):
            raise VectorIndexNeedsRebuild("记忆向量索引表缺失；停机后在实例目录执行 python -m len_bot.next.maintenance.memory_reindex")
        return row["dimensions"]

    def _create_vector_table(self, db: sqlite3.Connection, dimensions: int) -> None:
        settings = self.embedding.settings
        db.execute(f"CREATE VIRTUAL TABLE memory_vec USING vec0("
                   f"embedding float[{dimensions}] distance_metric=cosine, scope text)")
        db.execute("INSERT INTO memory_vector_binding(id,provider,base_url,model,dimensions) "
                   "VALUES(1,?,?,?,?)",
                   (settings.provider, settings.base_url, settings.model, dimensions))

    @staticmethod
    def _vector_bytes(vector: tuple[float, ...]) -> bytes:
        from sqlite_vec import serialize_float32

        return serialize_float32(list(vector))

    def _put_vector(self, db: sqlite3.Connection, rowid: int, scope: str,
                    vector: tuple[float, ...] | None, *, remove_existing: bool) -> None:
        if remove_existing and self._vector_table_exists(db):
            db.execute("DELETE FROM memory_vec WHERE rowid=?", (rowid,))
        if vector is not None:
            db.execute("INSERT INTO memory_vec(rowid, embedding, scope) VALUES(?,?,?)",
                       (rowid, self._vector_bytes(vector), scope))

    def _vector_preflight(self) -> int | None:
        with self._db() as db:
            return self._vector_binding(db)

    def index_status(self) -> dict:
        with self._db() as db:
            row = db.execute('SELECT base_url,model,dimensions FROM memory_vector_binding WHERE id=1').fetchone()
            stored = None if row is None else dict(row)
            configured = None if self.embedding is None else {
                'base_url': self.embedding.settings.base_url, 'model': self.embedding.settings.model,
                'dimensions': self.embedding.settings.dimensions,
            }
            reason = None
            needs = configured is None and stored is not None
            try:
                dimensions = self._vector_binding(db)
            except VectorIndexNeedsRebuild as error:
                dimensions, needs, reason = None, True, str(error)
            return {'stored': stored, 'configured': configured, 'needs_rebuild': needs, 'reason': reason,
                    'retrieval': 'hybrid' if dimensions is not None and not needs else 'text'}

    async def reindex_embeddings(self) -> int:
        """Explicit offline rebuild of text and vector derivations; preserve history."""
        if self.embedding is None:
            raise ValueError("reindex_embeddings requires configured embedding")
        files, summaries = self._source_files()
        ordered = sorted(files.items())
        partitions: dict[str, list[tuple[str, str]]] = {}
        for (scope, path), content in ordered:
            partitions.setdefault(scope, []).append((path, content))
        vectors: dict[tuple[str, str], tuple[float, ...]] = {}
        dimensions: int | None = None
        for scope, entries in partitions.items():
            for offset in range(0, len(entries), 100):
                selected = entries[offset:offset + 100]
                batch = await self._embedding(scope, [content for _, content in selected], "reindex")
                if dimensions is None:
                    dimensions = batch.dimensions
                elif dimensions != batch.dimensions:
                    raise ValueError(f'Memory reindex partitions have different embedding dimensions: '
                                     f'expected={dimensions}, scope={scope!r}, actual={batch.dimensions}; index not replaced')
                for index, (path, _) in enumerate(selected):
                    vectors[scope, path] = batch.vectors[index]
        with self._db() as db:
            db.execute("BEGIN EXCLUSIVE")
            for summary in summaries:
                summary.unlink()
            db.execute("DELETE FROM memory_summaries")
            if self._vector_table_exists(db):
                db.execute("DROP TABLE memory_vec")
            db.execute("DELETE FROM memory_vector_binding")
            old_rows = db.execute("SELECT id,content FROM memory_files").fetchall()
            for row in old_rows:
                self._remove_index(db, row)
            if dimensions is not None:
                self._create_vector_table(db, dimensions)
            for (scope, path), content in ordered:
                self._put_index(db, scope, path, content, None)
                rowid = db.execute("SELECT id FROM memory_files WHERE scope=? AND path=?",
                                   (scope, path)).fetchone()[0]
                self._put_vector(db, rowid, scope, vectors[scope, path], remove_existing=False)
        return len(ordered)

    def _browse_sync(self, scope: str, path: str, offset: int, limit: int) -> MemoryPage:
        directory = self._target(scope, path, file=False)
        if not directory.exists() and not path:
            return MemoryPage(nodes=(), has_more=False)
        if not directory.exists():
            raise FileNotFoundError(directory)
        if not directory.is_dir():
            raise NotADirectoryError(directory)
        entries = sorted((entry for entry in directory.iterdir()
                          if entry.is_dir() or (entry.suffix == ".md" and entry.name not in SUMMARY_FILES)),
                         key=lambda entry: (not entry.is_dir(), entry.name))
        shown = entries[offset:offset + limit]
        nodes = tuple(MemoryNode(path=f"{path}/{entry.name}" if path else entry.name,
                                 name=entry.name, is_dir=entry.is_dir())
                      for entry in shown)
        for node in nodes:
            self._target(scope, node.path, file=not node.is_dir)
        return MemoryPage(nodes=nodes, has_more=offset + limit < len(entries))

    async def browse(self, scene: str, path: str = "", *, scope: Literal["scene", "public"] = "scene",
                     offset: int = 0, limit: int = 20) -> MemoryPage:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("browse requires offset >= 0 and limit 1..100")
        source = self._source(scene, scope)
        async with self._lock(source):
            return await asyncio.to_thread(self._browse_sync, source, path, offset, limit)

    def _tree_sync(self, scope: str) -> list[dict]:
        root = self._target(scope, "", file=False)
        if not root.exists():
            return []
        files = []
        for target in sorted(root.rglob("*.md")):
            if target.name in SUMMARY_FILES:
                continue
            path = target.relative_to(root).as_posix()
            self._target(scope, path, file=True)
            files.append({"path": path, "chars": len(_source_text(target))})
        return files

    async def tree(self, scene: str) -> list[dict]:
        """Every memory file of the scene with its length, for one-request orientation."""
        source = self._source(scene, "scene")
        async with self._lock(source):
            return await asyncio.to_thread(self._tree_sync, source)

    def _read_sync(self, scope: str, path: str) -> MemoryDocument:
        target = self._target(scope, path, file=True)
        content = _source_text(target)
        with self._db() as db:
            self._indexed(db, scope, path, content)
        return MemoryDocument(path=path, content=content)

    async def read(self, scene: str, path: str, *,
                   scope: Literal["scene", "public"] = "scene") -> MemoryDocument:
        source = self._source(scene, scope)
        async with self._lock(source):
            return await asyncio.to_thread(self._read_sync, source, path)

    def _profiles_sync(self, scene: str, users: list[str]) -> list[MemoryDocument]:
        documents: list[MemoryDocument] = []
        for user in dict.fromkeys(users):
            if re.fullmatch(r"[a-z][a-z0-9_-]*:[^:\s/\\]+", user) is None:
                raise ValueError(f"profile account must be a platform sender identity: {user!r}")
            for filename in ("profile.md", "preferences.md"):
                path = f"people/{user}/{filename}"
                target = self._target(scene, path, file=True)
                if target.exists():
                    documents.append(self._read_sync(scene, path))
        return documents

    async def profiles(self, scene: str, users: list[str]) -> list[MemoryDocument]:
        if len(users) > 4:
            raise ValueError("profiles accepts at most four actual account senders")
        source = _scene_scope(scene)
        async with self._lock(source):
            return await asyncio.to_thread(self._profiles_sync, source, users)

    def _write_sync(self, scope: str, path: str, content: str, reason: str,
                    vector: tuple[float, ...] | None, create_only: bool = False) -> LocalMemoryChange:
        target = self._target(scope, path, file=True)
        if create_only and target.exists():
            raise FileExistsError(f'New formal memory never overwrites an existing file: {target}')
        before = _source_text(target) if target.exists() else None
        changed_at = time.time()
        with self._file_mutation(scope, path, 'write', changed_at, [target]) as db:
            self._mutation_preflight(db)
            dimensions = self._vector_binding(db)
            if vector is not None:
                if dimensions is None:
                    self._create_vector_table(db, len(vector))
                elif len(vector) != dimensions:
                    raise ValueError(f"memory vector dimension {len(vector)} differs from index {dimensions}")
            old = self._indexed(db, scope, path, before)
            _atomic_replace(target, content, create_only=create_only)
            self._put_index(db, scope, path, content, old)
            rowid = old["id"] if old is not None else db.execute(
                "SELECT id FROM memory_files WHERE scope=? AND path=?", (scope, path)).fetchone()[0]
            self._put_vector(db, rowid, scope, vector, remove_existing=old is not None)
            db.execute("INSERT INTO memory_changes(scope,path,changed_at,action,reason,before,after) "
                       "VALUES(?,?,?,?,?,?,?)", (scope, path, changed_at, "write", reason, before, content))
        return LocalMemoryChange("write", path, changed_at, reason, before, content)

    async def write(self, scene: str, path: str, content: str, reason: str, *, create_only: bool = False) -> LocalMemoryChange:
        if not reason.strip():
            raise ValueError("memory write reason must not be blank")
        source = _scene_scope(scene)
        async with self._lock(source):
            target = self._target(source, path, file=True)
            if create_only and target.exists():
                raise FileExistsError(f'New formal memory never overwrites an existing file: {target}')
            vector = await self._embed_text(source, content)
            return await _finish_write_thread(self._write_sync, source, path, content, reason, vector, create_only)

    async def owner_write_public(self, path: str, content: str, reason: str) -> LocalMemoryChange:
        """Management-only entry; the authenticated host must establish owner authority."""
        if not reason.strip():
            raise ValueError("public memory write reason must not be blank")
        async with self._lock("public"):
            self._target("public", path, file=True)
            vector = await self._embed_text("public", content)
            return await _finish_write_thread(self._write_sync, "public", path, content, reason, vector)

    async def _embedding(self, source: str, texts: list[str], purpose: str):
        if self.track_embedding is not None:
            return await self.track_embedding(source, texts, purpose)
        return await self.embedding.embed(texts)

    async def _embed_text(self, source: str, content: str) -> tuple[float, ...] | None:
        if self.embedding is None:
            return None
        dimensions = await asyncio.to_thread(self._vector_preflight)
        batch = await self._embedding(source, [content], "index")
        if dimensions is not None and batch.dimensions != dimensions:
            raise ValueError(f"embedding dimensions {batch.dimensions} differ from memory index {dimensions}")
        return batch.vectors[0]

    @staticmethod
    def _lexical_rows(db: sqlite3.Connection, scene: str, query: str,
                      limit: int, include_public: bool, exclude_pending: bool) -> list[sqlite3.Row]:
        folded = query.casefold()
        scopes = (scene, "public") if include_public else (scene,)
        placeholders = ",".join("?" for _ in scopes)
        eligible = " AND substr(path,1,14)!='legacy-import/'" if exclude_pending else ""
        if len(folded) >= 3:
            expression = '"' + folded.replace('"', '""') + '"'
            return db.execute(f"""
                SELECT m.scope, m.path
                FROM memory_fts JOIN memory_files AS m ON m.id=memory_fts.rowid
                WHERE memory_fts MATCH ? AND m.scope IN ({placeholders})
                  AND instr(m.content, ?) > 0 {eligible}
                ORDER BY CASE WHEN m.scope=? THEN 0 ELSE 1 END, m.path LIMIT ?
            """, (expression, *scopes, folded, scene, limit)).fetchall()
        return db.execute(f"""
            SELECT scope, path FROM memory_files
            WHERE scope IN ({placeholders}) AND instr(content, ?) > 0 {eligible}
            ORDER BY CASE WHEN scope=? THEN 0 ELSE 1 END, path LIMIT ?
        """, (*scopes, folded, scene, limit)).fetchall()

    @staticmethod
    def _automatic_rows(db: sqlite3.Connection, scene: str, query: str,
                        limit: int, include_public: bool, exclude_pending: bool) -> list[sqlite3.Row]:
        # The existing recall budget also bounds the number of literal query fragments.
        folded = query.casefold()[-1200:]
        if len(folded) < 3:
            return LocalMemory._lexical_rows(db, scene, folded, limit, include_public, exclude_pending)
        terms = dict.fromkeys(folded[index:index + 3] for index in range(len(folded) - 2))
        expression = ' OR '.join('"' + term.replace('"', '""') + '"' for term in terms)
        scopes = (scene, "public") if include_public else (scene,)
        placeholders = ",".join("?" for _ in scopes)
        eligible = " AND substr(path,1,14)!='legacy-import/'" if exclude_pending else ""
        # A short message remains searchable when a following reply adds another line.
        short = list(dict.fromkeys(line.strip() for line in folded.splitlines() if 0 < len(line.strip()) < 3))
        short_rows = ""
        parameters = [expression, *scopes]
        if short:
            matches = " OR ".join("instr(content, ?) > 0" for _ in short)
            short_rows = f"""UNION ALL SELECT scope, path, 0 FROM memory_files
                WHERE scope IN ({placeholders}) AND ({matches}) {eligible}"""
            parameters.extend([*scopes, *short])
        return db.execute(f"""
            WITH hits AS MATERIALIZED (
                SELECT m.scope, m.path, memory_fts.rank AS relevance
                FROM memory_fts JOIN memory_files AS m ON m.id=memory_fts.rowid
                WHERE memory_fts MATCH ? AND m.scope IN ({placeholders}) {eligible}
                {short_rows}
            )
            SELECT scope, path FROM hits GROUP BY scope, path
            ORDER BY CASE WHEN scope=? THEN 0 ELSE 1 END, MIN(relevance), path LIMIT ?
        """, (*parameters, scene, limit)).fetchall()

    def _search_sync(self, scene: str, query: str, limit: int,
                     include_public: bool, exclude_pending: bool, automatic: bool) -> list[LocalMemoryHit]:
        with self._db() as db:
            search = self._automatic_rows if automatic else self._lexical_rows
            rows = search(db, scene, query, limit, include_public, exclude_pending)
        hits: list[LocalMemoryHit] = []
        for row in rows:
            content = self._read_sync(row["scope"], row["path"]).content
            hits.append(LocalMemoryHit(scope="public" if row["scope"] == "public" else "scene",
                                       path=row["path"], preview=content[:240], total_chars=len(content)))
        return hits

    def _search_hybrid_sync(self, scene: str, query: str,
                            vector: tuple[float, ...], limit: int,
                            include_public: bool, exclude_pending: bool, automatic: bool) -> list[LocalMemoryHit]:
        with self._db() as db:
            dimensions = self._vector_binding(db)
            if dimensions is None or len(vector) != dimensions:
                raise ValueError(f"memory query vector dimension {len(vector)} differs from index {dimensions}")
            search = self._automatic_rows if automatic else self._lexical_rows
            lexical = search(db, scene, query, limit, include_public, exclude_pending)
            eligible = (" AND rowid IN (SELECT id FROM memory_files WHERE substr(path,1,14)!='legacy-import/')"
                        if exclude_pending else "")
            vector_rows: list[tuple[str, str, float]] = []
            query_bytes = self._vector_bytes(vector)
            for scope in ((scene, "public") if include_public else (scene,)):
                rows = db.execute(f"""
                    SELECT rowid, distance FROM memory_vec
                    WHERE embedding MATCH ? AND k=? AND scope=? {eligible} ORDER BY distance
                """, (query_bytes, limit, scope)).fetchall()
                for row in rows:
                    source = db.execute("SELECT scope,path FROM memory_files WHERE id=?",
                                        (row["rowid"],)).fetchone()
                    if source is None or source["scope"] != scope:
                        raise ValueError(f"memory vector row {row['rowid']} is outside its source scope")
                    vector_rows.append((scope, source["path"], row["distance"]))
        ranks: dict[tuple[str, str], float] = {}
        for position, row in enumerate(lexical, 1):
            key = (row["scope"], row["path"])
            ranks[key] = ranks.get(key, 0.0) + 1 / position
        for position, (scope, path, _) in enumerate(sorted(vector_rows, key=lambda row: row[2]), 1):
            key = (scope, path)
            ranks[key] = ranks.get(key, 0.0) + 1 / position
        ordered = sorted(ranks, key=lambda key: (-ranks[key], key[0] != scene, key[1]))[:limit]
        hits: list[LocalMemoryHit] = []
        for scope, path in ordered:
            content = self._read_sync(scope, path).content
            hits.append(LocalMemoryHit(scope="public" if scope == "public" else "scene",
                                       path=path, preview=content[:240], total_chars=len(content)))
        return hits

    async def search(self, scene: str, query: str, limit: int = 10, *,
                     include_public: bool = True, exclude_pending: bool = False,
                     automatic: bool = False, text_query: str | None = None) -> list[LocalMemoryHit]:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("search requires a nonblank query and limit 1..100")
        source = _scene_scope(scene)
        literal = query if text_query is None else text_query
        async with AsyncExitStack() as locks:
            await locks.enter_async_context(self._lock(source))
            if include_public:
                await locks.enter_async_context(self._lock("public"))
            if self.embedding is None:
                return await asyncio.to_thread(self._search_sync, source, literal, limit,
                                               include_public, exclude_pending, automatic)
            try:
                dimensions = await asyncio.to_thread(self._vector_preflight)
            except VectorIndexNeedsRebuild as error:
                log_event(logger, 'memory_text_retrieval', '记忆改用全文检索', level=logging.WARNING, error=error)
                return await asyncio.to_thread(self._search_sync, source, literal, limit,
                                               include_public, exclude_pending, automatic)
            if dimensions is None:
                return await asyncio.to_thread(self._search_sync, source, literal, limit,
                                               include_public, exclude_pending, automatic)
            try:
                batch = await self._embedding(source, [query], "query")
                if batch.dimensions != dimensions:
                    raise ValueError(f"embedding dimensions {batch.dimensions} differ from memory index {dimensions}")
            except (ValueError, TimeoutError) as error:
                log_event(logger, 'memory_text_retrieval', '记忆改用全文检索', level=logging.WARNING, error=error)
                return await asyncio.to_thread(self._search_sync, source, literal, limit,
                                               include_public, exclude_pending, automatic)
            return await asyncio.to_thread(self._search_hybrid_sync, source, literal,
                                           batch.vectors[0], limit, include_public, exclude_pending, automatic)

    def _history_sync(self, scope: str, path: str) -> list[LocalMemoryChange]:
        self._target(scope, path, file=True)
        with self._db() as db:
            rows = db.execute("SELECT action,path,changed_at,reason,before,after FROM memory_changes "
                              "WHERE scope=? AND path=? ORDER BY id", (scope, path)).fetchall()
        return [LocalMemoryChange(row["action"], row["path"], row["changed_at"], row["reason"],
                                  row["before"], row["after"]) for row in rows]

    async def history(self, scene: str, path: str) -> list[LocalMemoryChange]:
        source = _scene_scope(scene)
        async with self._lock(source):
            return await asyncio.to_thread(self._history_sync, source, path)

    def _delete_sync(self, scope: str, path: str, reason: str) -> LocalMemoryChange:
        target = self._target(scope, path, file=True)
        before = _source_text(target)
        changed_at = time.time()
        with self._file_mutation(scope, path, 'delete', changed_at, [target]) as db:
            self._mutation_preflight(db)
            old = self._indexed(db, scope, path, before)
            target.unlink()
            self._put_vector(db, old["id"], scope, None, remove_existing=True)
            self._remove_index(db, old)
            db.execute("INSERT INTO memory_changes(scope,path,changed_at,action,reason,before,after) "
                       "VALUES(?,?,?,?,?,?,NULL)", (scope, path, changed_at, "delete", reason, before))
        return LocalMemoryChange("delete", path, changed_at, reason, before, None)

    async def delete(self, scene: str, path: str, reason: str) -> LocalMemoryChange:
        if not reason.strip():
            raise ValueError("memory delete reason must not be blank")
        source = _scene_scope(scene)
        async with self._lock(source):
            return await _finish_write_thread(self._delete_sync, source, path, reason)

    def _forget_sync(self, scope: str, path: str) -> LocalMemoryForget:
        target = self._target(scope, path, file=True)
        before = _source_text(target) if target.exists() else None
        # Any ancestor summary may repeat the forgotten text; remove it before the file.
        removed_summaries = self._drop_summaries(scope, path)
        with self._file_mutation(scope, path, 'forget', time.time(), [target]) as db:
            self._mutation_preflight(db)
            old = self._indexed(db, scope, path, before)
            if before is not None:
                target.unlink()
            if old is not None:
                self._put_vector(db, old["id"], scope, None, remove_existing=True)
            self._remove_index(db, old)
            cursor = db.execute("DELETE FROM memory_changes WHERE scope=? AND path=?", (scope, path))
            removed_history = cursor.rowcount
        return LocalMemoryForget(path=path, removed_current=before is not None,
                                 removed_history_versions=removed_history,
                                 removed_summaries=removed_summaries)

    async def forget(self, scene: str, path: str) -> LocalMemoryForget:
        """Remove current file, index and accessible local versions; not chat logs/backups."""
        source = _scene_scope(scene)
        async with self._lock(source):
            return await _finish_write_thread(self._forget_sync, source, path)

    def _summary_directory(self, scope: str, path: str) -> Path:
        directory = self._target(scope, path, file=False)
        if not directory.exists():
            raise FileNotFoundError(f"memory directory does not exist: {scope} {path!r}")
        if not directory.is_dir():
            raise NotADirectoryError(f"memory path is not a directory: {scope} {path!r}")
        return directory

    def _summary_sync(self, scope: str, path: str) -> LocalMemorySummary:
        directory = self._target(scope, path, file=False)
        files = [directory / name for name in SUMMARY_FILES]
        present = [file.exists() for file in files]
        if all(present):
            abstract, overview = (_source_text(file) for file in files)
        elif not any(present):
            abstract = overview = None
        else:
            raise ValueError(f"incomplete memory summary files in {directory}: {dict(zip(SUMMARY_FILES, present))}")
        with self._db() as db:
            generated_at, changed_after = _summary_times(db, scope, path)
        if all(present) and generated_at is None:
            raise ValueError(f"memory summary has no generation time: {directory}")
        return LocalMemorySummary(path, abstract, overview, generated_at, changed_after)

    async def summary(self, scene: str, path: str = "", *,
                      scope: Literal["scene", "public"] = "scene") -> LocalMemorySummary:
        source = self._source(scene, scope)
        async with self._lock(source):
            return await asyncio.to_thread(self._summary_sync, source, path)

    def summary_text_sync(self, scene: str) -> str | None:
        """The current scene root overview, read once when a host builds its system text."""
        return scene_overview(self.root, scene)

    def _summary_inputs_sync(self, scope: str, path: str) -> dict:
        if path == PENDING_PREFIX.rstrip("/") or path.startswith(PENDING_PREFIX):
            raise ValueError("待确认旧记忆不能生成正式摘要；请先采用到正式记忆目录")
        directory = self._summary_directory(scope, path)
        files, directories = [], []
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = f"{path}/{entry.name}" if path else entry.name
            if relative == PENDING_PREFIX.rstrip("/"):
                continue
            if entry.is_dir():
                self._target(scope, relative, file=False)
                summary = self._summary_sync(scope, relative)
                directories.append({"name": entry.name,
                                    "abstract": summary.abstract if summary.changed_after is None else None})
            elif entry.suffix == ".md" and entry.name not in SUMMARY_FILES:
                files.append({"name": entry.name, "content": _source_text(self._target(scope, relative, file=True))})
        return {"path": path, "files": files, "directories": directories}

    async def summary_inputs(self, scene: str, path: str, *,
                             scope: Literal["scene", "public"] = "scene") -> dict:
        source = self._source(scene, scope)
        async with self._lock(source):
            return await asyncio.to_thread(self._summary_inputs_sync, source, path)

    def _write_summary_sync(self, scope: str, path: str, abstract: str, overview: str) -> LocalMemorySummary:
        directory = self._summary_directory(scope, path)
        generated_at = time.time()
        with self._file_mutation(scope, path, 'summary', generated_at,
                                 [directory / name for name in SUMMARY_FILES]) as db:
            _atomic_replace(directory / ".abstract.md", abstract)
            _atomic_replace(directory / ".overview.md", overview)
            db.execute("INSERT INTO memory_summaries(scope,path,generated_at) VALUES(?,?,?) "
                       "ON CONFLICT(scope,path) DO UPDATE SET generated_at=excluded.generated_at",
                       (scope, path, generated_at))
        return self._summary_sync(scope, path)

    async def write_summary(self, scene: str, path: str, abstract: str, overview: str, *,
                            scope: Literal["scene", "public"] = "scene") -> LocalMemorySummary:
        if not abstract.strip() or not overview.strip():
            raise ValueError("memory summary abstract and overview must not be blank")
        if len(abstract) > ABSTRACT_CHARS or len(overview) > OVERVIEW_CHARS:
            raise ValueError(f"memory summary exceeds {ABSTRACT_CHARS}/{OVERVIEW_CHARS} characters: "
                             f"{len(abstract)}/{len(overview)}")
        source = self._source(scene, scope)
        async with self._lock(source):
            return await _finish_write_thread(self._write_summary_sync, source, path, abstract, overview)

    async def clear_summary(self, scene: str, path: str, *,
                            scope: Literal["scene", "public"] = "scene") -> None:
        """Remove this directory's derived files when it has no source content."""
        source = self._source(scene, scope)
        def remove() -> None:
            directory = self._summary_directory(source, path)
            with self._file_mutation(source, path, 'clear-summary', time.time(),
                                     [directory / name for name in SUMMARY_FILES]) as db:
                for name in SUMMARY_FILES:
                    (directory / name).unlink(missing_ok=True)
                db.execute("DELETE FROM memory_summaries WHERE scope=? AND path=?", (source, path))
        async with self._lock(source):
            await _finish_write_thread(remove)

    def _drop_summaries(self, scope: str, path: str) -> tuple[str, ...]:
        parts = _parts(path, file=True)[:-1]
        removed = []
        for depth in range(len(parts), -1, -1):
            relative = "/".join(parts[:depth])
            directory = self._target(scope, relative, file=False)
            files = [directory / name for name in SUMMARY_FILES if (directory / name).exists()]
            with self._file_mutation(scope, relative, 'clear-summary', time.time(), files) as db:
                for file in files:
                    file.unlink()
                db.execute("DELETE FROM memory_summaries WHERE scope=? AND path=?", (scope, relative))
            if files:
                removed.append(relative)
        return tuple(removed)
