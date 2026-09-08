"""Scene-local, evidence-linked skill proposals and immutable revisions."""
from __future__ import annotations

import json
import uuid

from pydantic import BaseModel, ConfigDict, Field

from len_bot.cognition.jobs import SkillCandidate


class SkillDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    applicability: str = Field(min_length=1, max_length=1000)
    steps: list[str] = Field(min_length=1, max_length=12)
    verification: list[str] = Field(min_length=1, max_length=12)
    exclusions: list[str] = Field(default_factory=list, max_length=12)


# These authored methods are extracted from the existing worker's verification
# contract. They contain no group data and are not overwritten by learning.
AUTHORED_SKILLS = [
    ("fact_check", SkillDraft(name="外部事实核对", applicability="需要核实具体对象、日期与外部发布事实时",
        steps=["明确原对象和所求指标", "查当事方正式发布并读取正文", "按资料日期和适用条件对齐结论"],
        verification=["搜索摘要只用于定位", "图表数值须读取原图或原始表格", "冲突和未核实项明确保留"], exclusions=["缺少可靠原始来源时不能宣称已证实"])),
    ("finite_calculation", SkillDraft(name="给定条件计算与有限枚举", applicability="数据已给定，需算式核验或有限整数约束求解时",
        steps=["列明条件和变量范围", "用 calculate 检查数值", "最值和有限约束用 finite_check 检查候选与边界"],
        verification=["穷举范围覆盖原题", "最值同时需要可达例子与全范围界限"], exclusions=["不可把未经授权的额外假设作为原题条件"])),
    ("source_synthesis", SkillDraft(name="带来源资料整理", applicability="多份资料需要对照、整理或解释时",
        steps=["明确资料对象和时间范围", "保留来源引用并按需续读", "区分一致结论、冲突和信息空缺"],
        verification=["每个关键结论可回读原始来源", "未解决冲突不能省略"], exclusions=["不把压缩摘要作为新增证据"])),
]


class SkillStoreMixin:
    async def initialize_skills(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, scope TEXT NOT NULL,
            current_version INTEGER NOT NULL, author TEXT NOT NULL, updated_at REAL NOT NULL)""")
        await self._db.execute("""CREATE TABLE IF NOT EXISTS skill_versions (
            skill_id TEXT NOT NULL, version INTEGER NOT NULL, body_json TEXT NOT NULL,
            source_json TEXT NOT NULL, created_at REAL NOT NULL, scope TEXT NOT NULL, PRIMARY KEY(skill_id,version))""")
        await self._db.execute("""CREATE TABLE IF NOT EXISTS skill_candidates (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, job_id TEXT NOT NULL, job_revision INTEGER NOT NULL,
            candidate_json TEXT NOT NULL, status TEXT NOT NULL, error TEXT, skill_id TEXT,
            created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
        for ident, draft in AUTHORED_SKILLS:
            skill_id = "authored_" + ident
            await self._db.execute("INSERT OR IGNORE INTO skills VALUES(?,'global-safe','global-safe',1,'human',?)", (skill_id, self.clock()))
            await self._db.execute("INSERT OR IGNORE INTO skill_versions VALUES(?,1,?,?,?,'global-safe')",
                (skill_id, draft.model_dump_json(), json.dumps({"authored_source": "runtime/job_runner.py verification contract"}), self.clock()))
        # A request whose result was not committed is not silently repeated.
        await self._db.execute("UPDATE skill_candidates SET status='interrupted',error='维护请求未确认完成' WHERE status='processing'")

    async def list_skills(self, scene_id=None):
        params = (scene_id, scene_id)
        rows = await (await self._db.execute("""SELECT s.id,s.scene_id,v.scope,v.version,s.author,s.updated_at,v.body_json,v.source_json
            FROM skills s JOIN skill_versions v ON v.skill_id=s.id AND v.version=(SELECT MAX(p.version) FROM skill_versions p
                WHERE p.skill_id=s.id AND (? IS NULL OR s.scene_id=? OR p.scope='global-safe')) ORDER BY s.id""", params)).fetchall()
        return [{"id": r[0], "scene_id": r[1], "scope": r[2], "version": r[3], "author": r[4], "updated_at": r[5],
                 **json.loads(r[6]), "source": json.loads(r[7])} for r in rows]

    async def read_skill(self, skill_id, scene_id, version=None):
        row = await (await self._db.execute("""SELECT s.id,s.scene_id,v.scope,v.version,s.author,s.updated_at,v.body_json,v.source_json
            FROM skills s JOIN skill_versions v ON v.skill_id=s.id AND v.version=COALESCE(?,
                (SELECT MAX(p.version) FROM skill_versions p WHERE p.skill_id=s.id AND (s.scene_id=? OR p.scope='global-safe')))
            WHERE s.id=? AND (s.scene_id=? OR v.scope='global-safe')""", (version, scene_id, skill_id, scene_id))).fetchone()
        if row is None:
            return None
        return {"id": row[0], "scene_id": row[1], "scope": row[2], "version": row[3], "author": row[4], "updated_at": row[5],
                **json.loads(row[6]), "source": json.loads(row[7])}

    async def list_skill_candidates(self, scene_id=None):
        clause, params = ("", ()) if scene_id is None else (" WHERE scene_id=?", (scene_id,))
        rows = await (await self._db.execute("SELECT * FROM skill_candidates" + clause + " ORDER BY created_at,id", params)).fetchall()
        keys = ("id", "scene_id", "job_id", "job_revision", "candidate", "status", "error", "skill_id", "created_at", "updated_at")
        values = [dict(zip(keys, row)) for row in rows]
        for value in values:
            value["candidate"] = json.loads(value["candidate"])
        return values

    async def add_skill_candidate_in_transaction(self, job, candidate: SkillCandidate):
        if not set(candidate.result_ids).issubset(job["result_ids"]):
            raise ValueError("Skill candidate must cite this work's actual observations")
        for result_id in candidate.result_ids:
            observation = await self.read_tool_observation(result_id, [job["scene_id"]])
            if observation is None or observation.status not in {"ok", "partial", "error", "unsupported"}:
                raise ValueError("Skill evidence unavailable")
        if not set(candidate.correction_event_ids).issubset(job["source_event_ids"]):
            raise ValueError("Correction must locate original input to this work")
        for event_id in candidate.correction_event_ids:
            row = await (await self._db.execute("SELECT event_type FROM events WHERE id=? AND scene_id=?", (event_id, job["scene_id"]))).fetchone()
            if not row or row[0] not in {"GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED", "OPERATOR_ACTION"}:
                raise ValueError("Correction must be original human input")
        if candidate.skill_id:
            skill = await self.read_skill(candidate.skill_id, job["scene_id"])
            if not skill or skill["author"] != "agent" or skill["scene_id"] != job["scene_id"] or skill["version"] != candidate.expected_version:
                raise ValueError("Cannot revise foreign, authored or outdated skill")
        encoded = candidate.model_dump_json()
        existing = await (await self._db.execute("""SELECT id FROM skill_candidates
            WHERE scene_id=? AND job_id=? AND job_revision=? AND candidate_json=?
            ORDER BY created_at,id LIMIT 1""", (job["scene_id"], job["id"], job["revision"], encoded))).fetchone()
        if existing is not None:
            return existing[0]
        ident = "skill_candidate_" + uuid.uuid4().hex
        await self._db.execute("INSERT INTO skill_candidates VALUES(?,?,?,?,?,'pending',NULL,NULL,?,?)",
            (ident, job["scene_id"], job["id"], job["revision"], encoded, self.clock(), self.clock()))
        return ident

    async def set_skill_candidate_status(self, candidate_id, scene_id, status, error=None):
        async with self._write_lock:
            await self._db.execute("UPDATE skill_candidates SET status=?,error=?,updated_at=? WHERE id=? AND scene_id=?",
                (status, error, self.clock(), candidate_id, scene_id))
            await self._db.commit()

    async def save_skill_draft(self, candidate_id, scene_id, draft: SkillDraft):
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                row = await (await self._db.execute("SELECT job_id,job_revision,candidate_json,status FROM skill_candidates WHERE id=? AND scene_id=?", (candidate_id, scene_id))).fetchone()
                if not row or row[3] != "processing":
                    raise ValueError("Candidate is not awaiting a maintenance result")
                job = await self.get_job(row[0], scene_id)
                if not job or job["revision"] != row[1] or job["status"] == "cancelled":
                    raise ValueError("Candidate source work changed before commit")
                candidate = SkillCandidate.model_validate_json(row[2])
                skill_id = candidate.skill_id or "skill_" + uuid.uuid4().hex[:20]
                version = 1
                if candidate.skill_id:
                    old = await self.read_skill(skill_id, scene_id)
                    if not old or old["author"] != "agent" or old["scene_id"] != scene_id or old["version"] != candidate.expected_version:
                        raise ValueError("Skill version or ownership conflict")
                    version = old["version"] + 1
                    # An automatic revision stays local; publication of a prior
                    # version does not authorize publishing new group material.
                    await self._db.execute("UPDATE skills SET current_version=?,scope=?,updated_at=? WHERE id=?", (version, scene_id, self.clock(), skill_id))
                else:
                    await self._db.execute("INSERT INTO skills VALUES(?,?,?,1,'agent',?)", (skill_id, scene_id, scene_id, self.clock()))
                source = {"job_id": row[0], "job_revision": row[1], "candidate_id": candidate_id,
                          "result_ids": candidate.result_ids, "correction_event_ids": candidate.correction_event_ids}
                await self._db.execute("INSERT INTO skill_versions VALUES(?,?,?,?,?,?)", (skill_id, version, draft.model_dump_json(), json.dumps(source), self.clock(), scene_id))
                await self._db.execute("UPDATE skill_candidates SET status='saved',skill_id=?,updated_at=? WHERE id=?", (skill_id, self.clock(), candidate_id))
                await self._db.commit()
                return skill_id
            except BaseException:
                await self._db.rollback()
                raise

    async def publish_skill(self, skill_id, scene_id, expected_version):
        async with self._write_lock:
            cursor = await self._db.execute("UPDATE skills SET scope='global-safe',updated_at=? WHERE id=? AND scene_id=? AND current_version=?",
                (self.clock(), skill_id, scene_id, expected_version))
            if cursor.rowcount != 1:
                await self._db.rollback()
                raise ValueError("Skill version or scene conflict")
            await self._db.execute("UPDATE skill_versions SET scope='global-safe' WHERE skill_id=? AND version=?", (skill_id, expected_version))
            await self._db.commit()
