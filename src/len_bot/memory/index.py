"""Rebuildable vector index for committed memories only."""
from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable


class MemoryIndex:
    def __init__(self, db, write_lock, retrieval_models=None, profile=None, clock=time.time):
        self.db, self.write_lock, self.retrieval_models, self.profile, self.clock = (
            db, write_lock, retrieval_models, profile, clock
        )
        self._embedding_slots = asyncio.Semaphore(2)

    async def initialize(self):
        async with self.write_lock:
            await self.db.execute("""CREATE TABLE IF NOT EXISTS memory_index (
                scope TEXT NOT NULL, source_kind TEXT NOT NULL, source_id TEXT NOT NULL,
                source_revision INTEGER NOT NULL, profile_generation TEXT NOT NULL,
                dimension INTEGER NOT NULL, vector TEXT NOT NULL, indexed_at REAL NOT NULL,
                PRIMARY KEY(scope, source_kind, source_id))""")
            await self.db.execute("CREATE INDEX IF NOT EXISTS idx_memory_index_scope ON memory_index(scope, source_kind)")
            await self.db.commit()

    async def _index_texts(self, entries, *, scene_id: str, source_kind: str,
                           request_guard: Callable[[], bool] | None = None):
        profile = self.profile
        if not self.retrieval_models or not profile or not entries:
            return {"status": "disabled", "indexed": 0}
        if request_guard is not None and not request_guard():
            return {"status": "cancelled", "indexed": 0, "reason": "scene_opt_out"}
        texts = [item[1] for item in entries]
        try:
            async with self._embedding_slots:
                if request_guard is not None and not request_guard():
                    return {"status": "cancelled", "indexed": 0, "reason": "scene_opt_out"}
                vectors = await self.retrieval_models.embed(
                    profile, texts, scene_id=scene_id, purpose="embedding_document"
                )
        except Exception as error:
            return {"status": "error", "error_type": type(error).__name__, "error": str(error)}
        if request_guard is not None and not request_guard():
            return {"status": "cancelled", "indexed": 0, "reason": "scene_opt_out"}
        if self.profile != profile:
            return {"status": "stale_profile", "indexed": 0}
        if len(vectors) != len(entries) or any(not vector for vector in vectors):
            return {"status": "error", "error_type": "InvalidEmbedding", "error": "嵌入返回数量或向量为空"}
        generation = f"{profile.provider_id}:{profile.model}:{profile.dimension or len(vectors[0])}"
        indexed = 0
        async with self.write_lock:
            if request_guard is not None and not request_guard():
                return {"status": "cancelled", "indexed": 0, "reason": "scene_opt_out"}
            for (source_id, _text, revision), vector in zip(entries, vectors, strict=True):
                if source_kind == "memory":
                    current = await (await self.db.execute(
                        """SELECT 1 FROM memories WHERE id=? AND scope=? AND status='active'
                           AND (expires_at IS NULL OR expires_at>?) AND revision=?""",
                        (source_id, scene_id, self.clock(), revision),
                    )).fetchone()
                elif source_kind == "history_summary":
                    current = await (await self.db.execute(
                        """SELECT 1 FROM history_batches WHERE id=? AND scene_id=?
                           AND status='completed' AND generation_version=?""",
                        (source_id, scene_id, revision),
                    )).fetchone()
                else:
                    current = None
                if current is None:
                    continue
                await self.db.execute(
                    """INSERT INTO memory_index(scope,source_kind,source_id,source_revision,
                              profile_generation,dimension,vector,indexed_at)
                       VALUES(?,?,?,?,?,?,?,?)
                       ON CONFLICT(scope,source_kind,source_id) DO UPDATE SET
                         source_revision=excluded.source_revision,
                         profile_generation=excluded.profile_generation,
                         dimension=excluded.dimension, vector=excluded.vector,
                         indexed_at=excluded.indexed_at""",
                    (scene_id, source_kind, source_id, revision, generation,
                     len(vector), json.dumps(vector), self.clock()),
                )
                indexed += 1
            await self.db.commit()
        return {"status": "indexed", "indexed": indexed, "profile_generation": generation}

    async def index_memories(self, memories, *, scene_id: str, request_guard=None):
        return await self._index_texts(
            [(item.id, f"{item.kind.value}: {item.statement}", item.revision) for item in memories],
            scene_id=scene_id, source_kind="memory", request_guard=request_guard,
        )

    async def index_summary(self, batch_id: str, summary: str, generation: str | int, *, scene_id: str,
                            request_guard=None):
        return await self._index_texts(
            [(batch_id, summary, generation)], scene_id=scene_id,
            source_kind="history_summary", request_guard=request_guard,
        )

    async def candidates(self, scene_id, query_vector, *, limit=24, source_kind="memory",
                         subject=None, kind=None, include_superseded=False,
                         start_time=None, end_time=None, max_end_rowid=None):
        """Return top-k vectors from currently eligible source rows only."""
        params: list[object] = [scene_id]
        if source_kind == "memory":
            sql = """SELECT i.source_id,i.dimension,i.vector,i.profile_generation
                       FROM memory_index i JOIN memories m
                         ON m.id=i.source_id AND m.scope=i.scope
                      WHERE i.scope=? AND i.source_kind='memory'
                        AND i.source_revision=m.revision"""
            if not include_superseded:
                sql += " AND m.status='active' AND (m.expires_at IS NULL OR m.expires_at>?)"
                params.append(self.clock())
            if subject is not None:
                sql += " AND m.subject=?"; params.append(subject)
            if kind is not None:
                sql += " AND m.kind=?"; params.append(kind)
            if start_time is not None:
                sql += " AND m.created_at>=?"; params.append(start_time)
            if end_time is not None:
                sql += " AND m.created_at<?"; params.append(end_time)
        elif source_kind == "history_summary":
            sql = """SELECT i.source_id,i.dimension,i.vector,i.profile_generation
                       FROM memory_index i JOIN history_batches h
                         ON h.id=i.source_id AND h.scene_id=i.scope
                      WHERE i.scope=? AND i.source_kind='history_summary'
                        AND i.source_revision=h.generation_version AND h.status='completed'"""
            if max_end_rowid is not None:
                sql += " AND h.end_rowid<=?"; params.append(max_end_rowid)
        else:
            return []
        rows = await (await self.db.execute(sql, params)).fetchall()
        scored = []
        qnorm = math.sqrt(sum(x * x for x in query_vector)) or 1.0
        expected_prefix = f"{self.profile.provider_id}:{self.profile.model}:" if self.profile else None
        for source_id, dimension, encoded, generation in rows:
            if expected_prefix and not generation.startswith(expected_prefix):
                continue
            if self.profile and self.profile.dimension and generation != f"{expected_prefix}{self.profile.dimension}":
                continue
            if dimension != len(query_vector):
                continue
            vector = json.loads(encoded)
            denom = qnorm * (math.sqrt(sum(x * x for x in vector)) or 1.0)
            scored.append((sum(a * b for a, b in zip(query_vector, vector)) / denom, source_id))
        return [source_id for _, source_id in sorted(scored, reverse=True)[:limit]]

    def _profile_clause(self, alias="i"):
        if not self.profile:
            return "", []
        prefix = f"{self.profile.provider_id}:{self.profile.model}:"
        if self.profile.dimension:
            return f" AND {alias}.profile_generation=? AND {alias}.dimension=?", [f"{prefix}{self.profile.dimension}", self.profile.dimension]
        return f" AND {alias}.profile_generation LIKE ?", [f"{prefix}%"]

    async def coverage(self, scene_id):
        now = self.clock()
        total = (await (await self.db.execute(
            """SELECT COUNT(*) FROM memories WHERE scope=? AND status='active'
               AND (expires_at IS NULL OR expires_at>?)""", (scene_id, now),
        )).fetchone())[0]
        clause, extra = self._profile_clause()
        indexed = 0
        if self.profile:
            indexed = (await (await self.db.execute(
                """SELECT COUNT(*) FROM memory_index i JOIN memories m
                    ON m.id=i.source_id AND m.scope=i.scope AND i.source_revision=m.revision
                   WHERE i.scope=? AND i.source_kind='memory' AND m.status='active'
                     AND (m.expires_at IS NULL OR m.expires_at>?)""" + clause,
                [scene_id, now, *extra],
            )).fetchone())[0]
        return {"total": total, "indexed": indexed, "pending": max(0, total - indexed)}

    async def summary_coverage(self, scene_id):
        total = (await (await self.db.execute(
            "SELECT COUNT(*) FROM history_batches WHERE scene_id=? AND status='completed'", (scene_id,)
        )).fetchone())[0]
        clause, extra = self._profile_clause()
        indexed = 0
        if self.profile:
            indexed = (await (await self.db.execute(
                """SELECT COUNT(*) FROM memory_index i JOIN history_batches h
                    ON h.id=i.source_id AND h.scene_id=i.scope AND i.source_revision=h.generation_version
                   WHERE i.scope=? AND i.source_kind='history_summary' AND h.status='completed'""" + clause,
                [scene_id, *extra],
            )).fetchone())[0]
        return {"total": total, "indexed": indexed, "pending": max(0, total - indexed)}

    async def rebuild(self, scene_id: str, request_guard=None):
        """Explicit operator-triggered rebuild of derived vectors."""
        if not self.retrieval_models or not self.profile:
            return {"status": "disabled", "indexed": 0}
        if request_guard is not None and not request_guard():
            return {"status": "cancelled", "indexed": 0, "reason": "scene_opt_out"}
        now = self.clock()
        rows = await (await self.db.execute(
            """SELECT id,kind,statement,revision FROM memories
               WHERE scope=? AND status='active' AND (expires_at IS NULL OR expires_at>?) ORDER BY id""",
            (scene_id, now),
        )).fetchall()
        total = 0
        for start in range(0, len(rows), 32):
            result = await self._index_texts(
                [(row[0], f"{row[1]}: {row[2]}", row[3]) for row in rows[start:start + 32]],
                scene_id=scene_id, source_kind="memory", request_guard=request_guard,
            )
            if result.get("status") != "indexed":
                return {**result, "indexed": total}
            total += result.get("indexed", 0)
        summaries = await (await self.db.execute(
            "SELECT id,summary,generation_version FROM history_batches WHERE scene_id=? AND status='completed' ORDER BY id",
            (scene_id,),
        )).fetchall()
        for start in range(0, len(summaries), 32):
            result = await self._index_texts(
                [(row[0], row[1], row[2]) for row in summaries[start:start + 32]],
                scene_id=scene_id, source_kind="history_summary", request_guard=request_guard,
            )
            if result.get("status") != "indexed":
                return {**result, "indexed": total}
            total += result.get("indexed", 0)
        if request_guard is not None and not request_guard():
            return {"status": "cancelled", "indexed": total, "reason": "scene_opt_out"}
        async with self.write_lock:
            await self.db.execute(
                """DELETE FROM memory_index WHERE scope=? AND source_kind='memory'
                   AND NOT EXISTS (SELECT 1 FROM memories m WHERE m.id=memory_index.source_id
                     AND m.scope=memory_index.scope AND m.status='active'
                     AND (m.expires_at IS NULL OR m.expires_at>?)
                     AND m.revision=memory_index.source_revision)""",
                (scene_id, self.clock()),
            )
            await self.db.execute(
                """DELETE FROM memory_index WHERE scope=? AND source_kind='history_summary'
                   AND NOT EXISTS (SELECT 1 FROM history_batches h WHERE h.id=memory_index.source_id
                     AND h.scene_id=memory_index.scope AND h.status='completed'
                     AND h.generation_version=memory_index.source_revision)""",
                (scene_id,),
            )
            await self.db.commit()
        return {"status": "indexed", "indexed": total}
