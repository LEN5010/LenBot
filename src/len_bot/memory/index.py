"""Rebuildable vector index for committed memories only."""
from __future__ import annotations
import asyncio
import json, math, time

class MemoryIndex:
    def __init__(self, db, write_lock, retrieval_models=None, profile=None, clock=time.time):
        self.db, self.write_lock, self.retrieval_models, self.profile, self.clock = db, write_lock, retrieval_models, profile, clock
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

    async def _index_texts(self, entries, *, scene_id: str, source_kind: str):
        profile = self.profile
        if not self.retrieval_models or not profile or not entries: return {"status":"disabled","indexed":0}
        texts=[item[1] for item in entries]
        try:
            async with self._embedding_slots:
                vectors=await self.retrieval_models.embed(profile, texts, scene_id=scene_id, purpose="embedding_document")
        except Exception as error: return {"status":"error","error_type":type(error).__name__,"error":str(error)}
        if self.profile != profile:
            return {"status":"stale_profile","indexed":0}
        generation=f"{profile.provider_id}:{profile.model}:{profile.dimension or len(vectors[0])}"
        indexed=0
        async with self.write_lock:
            for (source_id, text, revision), vector in zip(entries, vectors):
                if source_kind == 'memory':
                    current = await (await self.db.execute(
                        "SELECT 1 FROM memories WHERE id=? AND scope=? AND status='active' AND revision=?", (source_id, scene_id, revision))).fetchone()
                elif source_kind == 'history_summary':
                    current = await (await self.db.execute(
                        "SELECT 1 FROM history_batches WHERE id=? AND scene_id=? AND status='completed' AND generation_version=?", (source_id, scene_id, revision))).fetchone()
                else:
                    current = None
                if current is None:
                    continue
                await self.db.execute("""INSERT INTO memory_index(scope,source_kind,source_id,source_revision,profile_generation,dimension,vector,indexed_at)
                    VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(scope,source_kind,source_id) DO UPDATE SET source_revision=excluded.source_revision,
                    profile_generation=excluded.profile_generation,dimension=excluded.dimension,vector=excluded.vector,indexed_at=excluded.indexed_at""",
                    (scene_id,source_kind,source_id,revision,generation,len(vector),json.dumps(vector),self.clock())); indexed+=1
            await self.db.commit()
        return {"status":"indexed","indexed":indexed,"profile_generation":generation}

    async def index_memories(self, memories, *, scene_id: str):
        return await self._index_texts([(item.id, f"{item.kind.value}: {item.statement}", item.revision) for item in memories], scene_id=scene_id, source_kind="memory")

    async def index_summary(self, batch_id: str, summary: str, generation: str | int, *, scene_id: str):
        return await self._index_texts([(batch_id, summary, generation)], scene_id=scene_id, source_kind="history_summary")

    async def candidates(self, scene_id, query_vector, *, limit=24, source_kind="memory"):
        rows=await (await self.db.execute("SELECT source_id,dimension,vector,profile_generation FROM memory_index WHERE scope=? AND source_kind=?",(scene_id,source_kind))).fetchall()
        scored=[]
        qnorm=math.sqrt(sum(x*x for x in query_vector)) or 1.0
        expected_prefix = f"{self.profile.provider_id}:{self.profile.model}:" if self.profile else None
        for source_id,dimension,encoded,generation in rows:
            if expected_prefix and not generation.startswith(expected_prefix): continue
            if self.profile and self.profile.dimension and generation != f"{expected_prefix}{self.profile.dimension}": continue
            if dimension != len(query_vector): continue
            vector=json.loads(encoded); denom=qnorm*(math.sqrt(sum(x*x for x in vector)) or 1.0)
            scored.append((sum(a*b for a,b in zip(query_vector,vector))/denom,source_id))
        return [source_id for _,source_id in sorted(scored,reverse=True)[:limit]]

    async def coverage(self, scene_id):
        total=(await (await self.db.execute("SELECT COUNT(*) FROM memories WHERE scope=? AND status='active'",(scene_id,))).fetchone())[0]
        if self.profile:
            pattern = f"{self.profile.provider_id}:{self.profile.model}:{self.profile.dimension}" if self.profile.dimension else f"{self.profile.provider_id}:{self.profile.model}:%"
            indexed=(await (await self.db.execute("SELECT COUNT(*) FROM memory_index WHERE scope=? AND source_kind='memory' AND profile_generation LIKE ?",(scene_id,pattern))).fetchone())[0]
        else:
            indexed=0
        return {"total":total,"indexed":indexed,"pending":max(0,total-indexed)}

    async def summary_coverage(self, scene_id):
        total=(await (await self.db.execute("SELECT COUNT(*) FROM history_batches WHERE scene_id=? AND status='completed'",(scene_id,))).fetchone())[0]
        if self.profile:
            pattern = f"{self.profile.provider_id}:{self.profile.model}:{self.profile.dimension}" if self.profile.dimension else f"{self.profile.provider_id}:{self.profile.model}:%"
            indexed=(await (await self.db.execute("SELECT COUNT(*) FROM memory_index WHERE scope=? AND source_kind='history_summary' AND profile_generation LIKE ?",(scene_id,pattern))).fetchone())[0]
        else:
            indexed=0
        return {"total":total,"indexed":indexed,"pending":max(0,total-indexed)}

    async def rebuild(self, scene_id: str):
        """Explicit operator-triggered rebuild of derived vectors."""
        if not self.retrieval_models or not self.profile:
            return {"status": "disabled", "indexed": 0}
        rows = await (await self.db.execute("SELECT id,kind,statement,revision FROM memories WHERE scope=? AND status='active' ORDER BY id", (scene_id,))).fetchall()
        total = 0
        for start in range(0, len(rows), 32):
            batch = rows[start:start + 32]
            result = await self._index_texts([(row[0], f"{row[1]}: {row[2]}", row[3]) for row in batch], scene_id=scene_id, source_kind='memory')
            if result.get('status') != 'indexed': return {**result, 'indexed': total}
            total += result.get('indexed', 0)
        summaries = await (await self.db.execute("SELECT id,summary,generation_version FROM history_batches WHERE scene_id=? AND status='completed' ORDER BY id", (scene_id,))).fetchall()
        for start in range(0, len(summaries), 32):
            batch = summaries[start:start + 32]
            result = await self._index_texts([(row[0], row[1], row[2]) for row in batch], scene_id=scene_id, source_kind='history_summary')
            if result.get('status') != 'indexed': return {**result, 'indexed': total}
            total += result.get('indexed', 0)
        active_ids = [row[0] for row in rows]
        summary_ids = [row[0] for row in summaries]
        async with self.write_lock:
            if active_ids:
                await self.db.execute("DELETE FROM memory_index WHERE scope=? AND source_kind='memory' AND source_id NOT IN (SELECT value FROM json_each(?))", (scene_id, json.dumps(active_ids)))
            else:
                await self.db.execute("DELETE FROM memory_index WHERE scope=? AND source_kind='memory'", (scene_id,))
            if summary_ids:
                await self.db.execute("DELETE FROM memory_index WHERE scope=? AND source_kind='history_summary' AND source_id NOT IN (SELECT value FROM json_each(?))", (scene_id, json.dumps(summary_ids)))
            else:
                await self.db.execute("DELETE FROM memory_index WHERE scope=? AND source_kind='history_summary'", (scene_id,))
            await self.db.commit()
        return {"status": "indexed", "indexed": total}
