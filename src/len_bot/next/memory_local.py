"""File-authoritative scene memory with explicit text or hybrid retrieval.

Embeddings are optional and separately configured; this module does not
extract facts from a conversation.
"""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import tempfile
import time
from collections.abc import Callable
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Literal, TypeVar

from pydantic import BaseModel, ConfigDict

from .memory_embeddings import EmbeddingBinding, EmbeddingClient
from .memory_types import MemoryDocument, MemoryNode, MemoryPage


_SCENE = re.compile(r"(group|private):([1-9][0-9]*)\Z")
_INDEX_NAME = ".memory-index.sqlite3"
# Derived directory summaries: L0 abstract and L1 overview, never indexed content.
SUMMARY_FILES = (".abstract.md", ".overview.md")
ABSTRACT_CHARS = 256
OVERVIEW_CHARS = 4000
_APPLICATION_ID = 0x4C424D31
_Result = TypeVar("_Result")


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
        raise ValueError(f"scene must be an existing group:<QQ> or private:<QQ> identity: {scene!r}")
    return scene


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


def _atomic_replace(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".memory-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content.encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def scene_overview(root: Path, scene: str) -> str | None:
    """Read a scene partition's root overview without opening the index (also used offline)."""
    category, qq = _SCENE.fullmatch(_scene_scope(scene)).groups()
    base = root.expanduser().resolve() / ("groups" if category == "group" else "private") / qq
    files = [base / name for name in SUMMARY_FILES]
    for path in (base, *files):
        if path.is_symlink():
            raise ValueError(f"memory summary path is a symlink: {path}")
    present = [file.exists() for file in files]
    if not any(present):
        return None
    if not all(present):
        raise ValueError(f"incomplete memory summary files in {base}: {dict(zip(SUMMARY_FILES, present))}")
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
        self.root = settings.directory.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index = self.root / _INDEX_NAME
        if self.index.is_symlink():
            raise ValueError(f"memory index must not be a symlink: {self.index}")
        self._locks: dict[str, asyncio.Lock] = {}
        self._initialize()

    def _lock(self, scope: str) -> asyncio.Lock:
        return self._locks.setdefault(scope, asyncio.Lock())

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.index)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA foreign_keys=ON")
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
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if application_id not in {0, _APPLICATION_ID} or version not in {0, 2}:
                raise ValueError(f"unsupported local memory index format at {self.index}: "
                                 f"application_id={application_id}, user_version={version}")
            if version == 0:
                db.executescript("""
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
                """)
                db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                db.execute("PRAGMA user_version=2")

    def _base(self, scope: str) -> Path:
        if scope == "public":
            return self.root / "public"
        match = _SCENE.fullmatch(scope)
        if match is None:
            raise ValueError(f"invalid memory source scope: {scope!r}")
        category, qq = match.groups()
        return self.root / ("groups" if category == "group" else "private") / qq

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
        target = base.joinpath(*parts)
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

    def _source_files(self) -> dict[tuple[str, str], str]:
        files: dict[tuple[str, str], str] = {}
        for category in ("public", "groups", "private"):
            directory = self.root / category
            if not directory.exists():
                continue
            if directory.is_symlink():
                raise ValueError(f"memory source root is a symlink: {directory}")
            for path in directory.rglob("*.md"):
                if path.name in SUMMARY_FILES:
                    continue
                if category == "public":
                    scope, relative = "public", path.relative_to(directory).as_posix()
                else:
                    position = path.relative_to(directory).parts
                    if len(position) < 2 or re.fullmatch(r"[1-9][0-9]*", position[0]) is None:
                        raise ValueError(f"unexpected memory source path: {path}")
                    scope = f"{'group' if category == 'groups' else 'private'}:{position[0]}"
                    relative = Path(*position[1:]).as_posix()
                actual = self._target(scope, relative, file=True)
                files[(scope, relative)] = _source_text(actual)
        return files

    def reindex(self) -> int:
        """Offline-only text rebuild; turn off any old vector index explicitly."""
        if self.embedding is not None:
            raise ValueError("configured embedding requires await reindex_embeddings() offline")
        files = self._source_files()
        with self._db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='memory_vec'").fetchone() is not None:
                self._load_vec(db)
            db.execute("BEGIN EXCLUSIVE")
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
                raise ValueError("configured embedding has existing Markdown without vectors; "
                                 "run reindex_embeddings() offline")
            return None
        settings = self.embedding.settings
        if (row["provider"] != settings.provider or row["base_url"] != settings.base_url
                or row["model"] != settings.model
                or (settings.dimensions is not None and row["dimensions"] != settings.dimensions)):
            raise ValueError("memory vector index uses a different provider URL/model/dimension; "
                             "run reindex_embeddings() offline")
        if not self._vector_table_exists(db):
            raise ValueError("memory vector index table is missing; run reindex_embeddings() offline")
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

    async def reindex_embeddings(self) -> int:
        """Explicit offline rebuild of text and vector derivations; preserve history."""
        if self.embedding is None:
            raise ValueError("reindex_embeddings requires configured embedding")
        files = self._source_files()
        ordered = sorted(files.items())
        batch = await self.embedding.embed([content for _, content in ordered]) if ordered else None
        with self._db() as db:
            db.execute("BEGIN EXCLUSIVE")
            if self._vector_table_exists(db):
                db.execute("DROP TABLE memory_vec")
            db.execute("DELETE FROM memory_vector_binding")
            old_rows = db.execute("SELECT id,content FROM memory_files").fetchall()
            for row in old_rows:
                self._remove_index(db, row)
            if batch is not None:
                self._create_vector_table(db, batch.dimensions)
            for index, ((scope, path), content) in enumerate(ordered):
                self._put_index(db, scope, path, content, None)
                if batch is not None:
                    rowid = db.execute("SELECT id FROM memory_files WHERE scope=? AND path=?",
                                       (scope, path)).fetchone()[0]
                    self._put_vector(db, rowid, scope, batch.vectors[index], remove_existing=False)
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

    def _profiles_sync(self, scene: str, qqs: list[str]) -> list[MemoryDocument]:
        documents: list[MemoryDocument] = []
        for qq in dict.fromkeys(qqs):
            if re.fullmatch(r"[1-9][0-9]*", qq) is None:
                raise ValueError(f"profile QQ must be a real numeric sender identity: {qq!r}")
            for filename in ("profile.md", "preferences.md"):
                path = f"people/{qq}/{filename}"
                target = self._target(scene, path, file=True)
                if target.exists():
                    documents.append(self._read_sync(scene, path))
        return documents

    async def profiles(self, scene: str, qqs: list[str]) -> list[MemoryDocument]:
        if len(qqs) > 4:
            raise ValueError("profiles accepts at most four actual QQ senders")
        source = _scene_scope(scene)
        async with self._lock(source):
            return await asyncio.to_thread(self._profiles_sync, source, qqs)

    def _write_sync(self, scope: str, path: str, content: str, reason: str,
                    vector: tuple[float, ...] | None) -> LocalMemoryChange:
        target = self._target(scope, path, file=True)
        before = _source_text(target) if target.exists() else None
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._mutation_preflight(db)
            dimensions = self._vector_binding(db)
            if vector is not None:
                if dimensions is None:
                    self._create_vector_table(db, len(vector))
                elif len(vector) != dimensions:
                    raise ValueError(f"memory vector dimension {len(vector)} differs from index {dimensions}")
            old = self._indexed(db, scope, path, before)
            _atomic_replace(target, content)
            self._put_index(db, scope, path, content, old)
            rowid = old["id"] if old is not None else db.execute(
                "SELECT id FROM memory_files WHERE scope=? AND path=?", (scope, path)).fetchone()[0]
            self._put_vector(db, rowid, scope, vector, remove_existing=old is not None)
            changed_at = time.time()
            db.execute("INSERT INTO memory_changes(scope,path,changed_at,action,reason,before,after) "
                       "VALUES(?,?,?,?,?,?,?)", (scope, path, changed_at, "write", reason, before, content))
        return LocalMemoryChange("write", path, changed_at, reason, before, content)

    async def write(self, scene: str, path: str, content: str, reason: str) -> LocalMemoryChange:
        if not reason.strip():
            raise ValueError("memory write reason must not be blank")
        source = _scene_scope(scene)
        async with self._lock(source):
            self._target(source, path, file=True)
            vector = await self._embed_text(content)
            return await _finish_write_thread(self._write_sync, source, path, content, reason, vector)

    async def owner_write_public(self, path: str, content: str, reason: str) -> LocalMemoryChange:
        """Management-only entry; the authenticated host must establish owner authority."""
        if not reason.strip():
            raise ValueError("public memory write reason must not be blank")
        async with self._lock("public"):
            self._target("public", path, file=True)
            vector = await self._embed_text(content)
            return await _finish_write_thread(self._write_sync, "public", path, content, reason, vector)

    async def _embed_text(self, content: str) -> tuple[float, ...] | None:
        if self.embedding is None:
            return None
        dimensions = await asyncio.to_thread(self._vector_preflight)
        batch = await self.embedding.embed([content])
        if dimensions is not None and batch.dimensions != dimensions:
            raise ValueError(f"embedding dimensions {batch.dimensions} differ from memory index {dimensions}")
        return batch.vectors[0]

    @staticmethod
    def _lexical_rows(db: sqlite3.Connection, scene: str, query: str,
                      limit: int, include_public: bool) -> list[sqlite3.Row]:
        folded = query.casefold()
        scopes = (scene, "public") if include_public else (scene,)
        placeholders = ",".join("?" for _ in scopes)
        if len(folded) >= 3:
            expression = '"' + folded.replace('"', '""') + '"'
            return db.execute(f"""
                SELECT m.scope, m.path
                FROM memory_fts JOIN memory_files AS m ON m.id=memory_fts.rowid
                WHERE memory_fts MATCH ? AND m.scope IN ({placeholders})
                  AND instr(m.content, ?) > 0
                ORDER BY CASE WHEN m.scope=? THEN 0 ELSE 1 END, m.path LIMIT ?
            """, (expression, *scopes, folded, scene, limit)).fetchall()
        return db.execute(f"""
            SELECT scope, path FROM memory_files
            WHERE scope IN ({placeholders}) AND instr(content, ?) > 0
            ORDER BY CASE WHEN scope=? THEN 0 ELSE 1 END, path LIMIT ?
        """, (*scopes, folded, scene, limit)).fetchall()

    def _search_sync(self, scene: str, query: str, limit: int,
                     include_public: bool) -> list[LocalMemoryHit]:
        with self._db() as db:
            rows = self._lexical_rows(db, scene, query, limit, include_public)
        hits: list[LocalMemoryHit] = []
        for row in rows:
            content = self._read_sync(row["scope"], row["path"]).content
            hits.append(LocalMemoryHit(scope="public" if row["scope"] == "public" else "scene",
                                       path=row["path"], preview=content[:240], total_chars=len(content)))
        return hits

    def _search_hybrid_sync(self, scene: str, query: str,
                            vector: tuple[float, ...], limit: int,
                            include_public: bool) -> list[LocalMemoryHit]:
        with self._db() as db:
            dimensions = self._vector_binding(db)
            if dimensions is None or len(vector) != dimensions:
                raise ValueError(f"memory query vector dimension {len(vector)} differs from index {dimensions}")
            lexical = self._lexical_rows(db, scene, query, limit, include_public)
            vector_rows: list[tuple[str, str, float]] = []
            query_bytes = self._vector_bytes(vector)
            for scope in ((scene, "public") if include_public else (scene,)):
                rows = db.execute("""
                    SELECT rowid, distance FROM memory_vec
                    WHERE embedding MATCH ? AND k=? AND scope=? ORDER BY distance
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
                     include_public: bool = True) -> list[LocalMemoryHit]:
        if not query.strip() or not 1 <= limit <= 100:
            raise ValueError("search requires a nonblank query and limit 1..100")
        source = _scene_scope(scene)
        async with AsyncExitStack() as locks:
            await locks.enter_async_context(self._lock(source))
            if include_public:
                await locks.enter_async_context(self._lock("public"))
            if self.embedding is None:
                return await asyncio.to_thread(self._search_sync, source, query, limit, include_public)
            dimensions = await asyncio.to_thread(self._vector_preflight)
            if dimensions is None:
                return []
            batch = await self.embedding.embed([query])
            if batch.dimensions != dimensions:
                raise ValueError(f"embedding dimensions {batch.dimensions} differ from memory index {dimensions}")
            return await asyncio.to_thread(self._search_hybrid_sync, source, query,
                                           batch.vectors[0], limit, include_public)

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
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._mutation_preflight(db)
            old = self._indexed(db, scope, path, before)
            target.unlink()
            self._put_vector(db, old["id"], scope, None, remove_existing=True)
            self._remove_index(db, old)
            changed_at = time.time()
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
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
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
            generated_at = min(file.stat().st_mtime for file in files)
        elif not any(present):
            abstract = overview = generated_at = None
        else:
            raise ValueError(f"incomplete memory summary files in {directory}: {dict(zip(SUMMARY_FILES, present))}")
        prefix = f"{path}/" if path else ""
        with self._db() as db:
            latest = db.execute("SELECT MAX(changed_at) FROM memory_changes WHERE scope=? AND substr(path,1,?)=?",
                                (scope, len(prefix), prefix)).fetchone()[0]
        changed_after = latest if latest is not None and (generated_at is None or latest > generated_at) else None
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
        directory = self._summary_directory(scope, path)
        files, directories = [], []
        for entry in sorted(directory.iterdir(), key=lambda item: item.name):
            relative = f"{path}/{entry.name}" if path else entry.name
            if entry.is_dir():
                self._target(scope, relative, file=False)
                abstract = entry / ".abstract.md"
                directories.append({"name": entry.name,
                                    "abstract": _source_text(abstract) if abstract.exists() else None})
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
        _atomic_replace(directory / ".abstract.md", abstract)
        _atomic_replace(directory / ".overview.md", overview)
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

    def _drop_summaries(self, scope: str, path: str) -> tuple[str, ...]:
        parts = _parts(path, file=True)[:-1]
        removed = []
        for depth in range(len(parts), -1, -1):
            relative = "/".join(parts[:depth])
            directory = self._target(scope, relative, file=False)
            files = [directory / name for name in SUMMARY_FILES if (directory / name).exists()]
            for file in files:
                file.unlink()
            if files:
                removed.append(relative)
        return tuple(removed)
