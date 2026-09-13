"""Durable accounting for actual provider requests, without a billing claim."""

from __future__ import annotations

import json
import uuid

from len_bot.cognition.projection import estimate_tokens


def estimate_request(messages: list[dict], tools: list[dict]) -> dict:
    """One deliberately approximate estimator; image estimates are explicit."""
    parts = {"system_reference": 0, "history": 0, "tool_results": 0,
             "tool_definitions": estimate_tokens(json.dumps(tools, ensure_ascii=False)), "images": 0}
    images = 0
    for message in messages:
        text = {key:value for key,value in message.items() if key != '_context_section'}
        content = message.get("content")
        if isinstance(content, list):
            text["content"] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") in {"image_url", "input_image"}:
                    images += 1
                else:
                    text["content"].append(item)
        key = "system_reference" if (message.get("role") in {"system", "developer"}
            or message.get('_context_section') == 'reference') else (
            "tool_results" if message.get("role") == "tool" else "history")
        parts[key] += estimate_tokens(json.dumps(text, ensure_ascii=False, default=str))
    parts["images"] = images * 1024
    return {"method": "cjk-1.5-other-1/3-v1", "parts": parts,
            "input_tokens": sum(parts.values()), "image_count": images, "image_tokens_each": 1024}


def measured_call_tokens(usage, estimate: dict, *, conservative_output_tokens: int) -> tuple[int, int]:
    """Split one call's tokens into billed usage and local estimate.

    Input and output each count once.  Cached tokens are already inside
    `prompt_tokens` and reasoning tokens are already inside
    `completion_tokens`, so neither is added again.  A call whose provider
    returned no usable usage is never counted as zero: its own local estimate
    stands in, and its output is bounded by the work's configured output
    limit.  The two totals stay separable so no view can call an estimate
    real billing.  A half-reported usage is not split between the two: an
    input without its matching output cannot be attributed, so that call is
    kept as an estimate rather than guessed at.
    """
    if isinstance(usage, dict):
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if (isinstance(prompt, (int, float)) and not isinstance(prompt, bool)
                and isinstance(completion, (int, float)) and not isinstance(completion, bool)):
            # prompt_tokens + completion_tokens is the whole metered exchange.
            return max(0, int(prompt) + int(completion)), 0
    estimated = int(estimate.get("input_tokens") or 0) + max(0, int(conservative_output_tokens))
    return 0, max(0, estimated)


class ModelCallStoreMixin:
    async def initialize_model_calls(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS model_calls (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, episode_id TEXT, job_id TEXT, batch_id TEXT,
            role TEXT NOT NULL, purpose TEXT NOT NULL, provider_id TEXT NOT NULL, model TEXT NOT NULL,
            reasoning_effort TEXT, started_at REAL NOT NULL, ended_at REAL,
            status TEXT NOT NULL, usage_json TEXT, estimate_json TEXT NOT NULL,
            output_estimate_tokens INTEGER, error_type TEXT, disposition TEXT)""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_model_calls_scene ON model_calls(scene_id,started_at)")
        # The execution-time token stop reads one work's calls on every model
        # request.  Without this index that read scans the whole call ledger
        # each time, so the index is what keeps the budget check cheap rather
        # than a caching layer over it.
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_model_calls_job ON model_calls(job_id,started_at)")
        # One row per accepted work: the quota it holds until it settles.  The
        # actual provider usage still lives in model_calls; this table only
        # answers "how much of today's balance is already spoken for".
        await self._db.execute("""CREATE TABLE IF NOT EXISTS usage_reservations (
            job_id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, subject TEXT NOT NULL,
            day_key TEXT NOT NULL, policy_name TEXT, reserved_tokens INTEGER NOT NULL,
            status TEXT NOT NULL, usage_tokens INTEGER, estimated_tokens INTEGER,
            created_at REAL NOT NULL, settled_at REAL)""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_usage_reservations_day ON usage_reservations(subject,day_key)")

    async def account_used_tokens_in_transaction(self, subject: str, day_key: str, *,
                                                 scene_id: str | None = None,
                                                 exclude_job_id: str | None = None) -> int:
        """Held plus already-settled tokens for one account on one day.

        `exclude_job_id` leaves one work out of the total.  A work that is put
        back on hold is re-checked against the same day, and its own earlier
        settlement is exactly what the new hold replaces; counting both would
        charge one work twice.
        """
        column, value = ("subject", subject) if scene_id is None else ("scene_id", scene_id)
        row = await (await self._db.execute(
            f"""SELECT COALESCE(SUM(CASE WHEN status='held' THEN reserved_tokens
                    ELSE COALESCE(usage_tokens,0)+COALESCE(estimated_tokens,0) END),0)
                FROM usage_reservations WHERE {column}=? AND day_key=? AND status IN ('held','settled')
                AND (? IS NULL OR job_id!=?)""",
            (value, day_key, exclude_job_id, exclude_job_id))).fetchone()
        return int(row[0] or 0)

    async def reserve_work_in_transaction(self, *, job_id, scene_id, subject, day_key, tokens,
                                          policy_name=None, scene_limit=None, daily_limit=None):
        """Hold one work's budget inside the caller's existing write transaction."""
        if tokens < 0:
            raise ValueError('A work reservation cannot be negative')
        if daily_limit is not None:
            used = await self.account_used_tokens_in_transaction(subject, day_key)
            if used + tokens > daily_limit:
                raise ValueError(
                    f'每日额度不足：该账号 {day_key} 已占用 {used}，本次工作需要预占 {tokens}，上限 {daily_limit}；'
                    '可以明确要求一个较小的工作范围，运行时不会静默改写目标或额度')
        if scene_id is not None and scene_limit is not None:
            used = await self.account_used_tokens_in_transaction(subject, day_key, scene_id=scene_id)
            if used + tokens > scene_limit:
                raise ValueError(
                    f'本群额度不足：{scene_id} 在 {day_key} 已占用 {used}，本次工作需要预占 {tokens}，上限 {scene_limit}')
        await self._db.execute("""INSERT INTO usage_reservations
            (job_id,scene_id,subject,day_key,policy_name,reserved_tokens,status,created_at)
            VALUES(?,?,?,?,?,?,'held',?)""",
            (job_id, scene_id, subject, day_key, policy_name, tokens, self.clock()))

    async def rehold_work_in_transaction(self, *, job_id, tokens, policy_name=None,
                                        scene_limit=None, daily_limit=None):
        """Put an ended work's budget back on hold, on its own reservation day.

        A settled hold is what the work spent, not what it may spend: read as
        a ceiling it would equal the work's own consumption and stop the next
        revision on its first call.  A released hold is only the amount given
        back.  Both are re-opened here to the work's cumulative ceiling,
        keeping one row per work and keeping the consumption attributed to
        the day the work was accepted instead of moving a past day's account
        into today.  Past consumption is never refunded: the work's ceiling
        does not grow, and everything it already spent still counts against
        the account.
        """
        if tokens < 0:
            raise ValueError('A work reservation cannot be negative')
        current = await (await self._db.execute(
            "SELECT scene_id,subject,day_key,status FROM usage_reservations WHERE job_id=?", (job_id,))).fetchone()
        if current is None:
            raise ValueError('This work has no reservation to re-hold')
        scene_id, subject, day_key, status = current
        if status == 'held':
            # A control can land while the work is still running; its original
            # hold never ended, so there is nothing to reopen.
            return
        if status not in {'settled', 'released'}:
            raise ValueError('This work has no settlement to re-hold')
        if daily_limit is not None:
            used = await self.account_used_tokens_in_transaction(subject, day_key, exclude_job_id=job_id)
            if used + tokens > daily_limit:
                raise ValueError(
                    f'每日额度不足：该账号 {day_key} 已占用 {used}（不含本工作），要重新预占 {tokens}，上限 {daily_limit}；'
                    '继续旧工作不新增额度，需要更多额度请由运营者明确调整策略或另立一个范围更小的工作')
        if scene_limit is not None:
            used = await self.account_used_tokens_in_transaction(subject, day_key, scene_id=scene_id, exclude_job_id=job_id)
            if used + tokens > scene_limit:
                raise ValueError(
                    f'本群额度不足：{scene_id} 在 {day_key} 已占用 {used}（不含本工作），要重新预占 {tokens}，上限 {scene_limit}')
        await self._db.execute("""UPDATE usage_reservations SET reserved_tokens=?,status='held',
            policy_name=?,settled_at=NULL WHERE job_id=? AND status IN ('settled','released')""",
            (tokens, policy_name, job_id))

    async def release_reservation_in_transaction(self, job_id: str, reason: str = ''):
        """Give back a hold whose work never consumed anything."""
        await self._db.execute("""UPDATE usage_reservations SET status='released',settled_at=?
            WHERE job_id=? AND status='held'""", (self.clock(), job_id))

    async def job_measured_tokens(self, job_id: str, *, conservative_output_tokens: int) -> tuple[int, int]:
        """What this work has spent so far, split into real usage and estimate.

        Every call carrying the job id counts, including compression, skill
        maintenance and plugin sub-agents: one work has one account whether it
        is being settled or merely read while it still runs.
        """
        rows = await (await self._db.execute(
            "SELECT usage_json,estimate_json FROM model_calls WHERE job_id=?", (job_id,))).fetchall()
        usage_tokens = estimated_tokens = 0
        for encoded_usage, encoded_estimate in rows:
            usage = json.loads(encoded_usage) if encoded_usage else None
            estimate = json.loads(encoded_estimate) if encoded_estimate else {}
            measured, estimated = measured_call_tokens(usage, estimate,
                conservative_output_tokens=conservative_output_tokens)
            usage_tokens += measured
            estimated_tokens += estimated
        return usage_tokens, estimated_tokens

    async def settle_reservation_in_transaction(self, job_id, *, conservative_output_tokens: int):
        """Replace a hold with what the work actually spent, keeping the split.

        Every model call carrying this job id counts here, including
        compression, skill maintenance and plugin sub-agents, so one work has
        one account.  A provider that returned no usage is recorded from its
        local estimate instead of being charged as zero.
        """
        usage_tokens, estimated_tokens = await self.job_measured_tokens(
            job_id, conservative_output_tokens=conservative_output_tokens)
        await self._db.execute("""UPDATE usage_reservations SET status='settled',usage_tokens=?,
            estimated_tokens=?,reserved_tokens=?,settled_at=? WHERE job_id=? AND status='held'""",
            (usage_tokens, estimated_tokens, usage_tokens + estimated_tokens, self.clock(), job_id))

    async def close_reservation_in_transaction(self, job_id, *, conservative_output_tokens: int):
        """End a hold: release it free, or settle it if the work already billed.

        A work cancelled before its first model call gives the whole hold back.
        One that already called a provider keeps those real tokens on its
        account, so cancelling is never a way to erase spent budget.
        """
        row = await (await self._db.execute(
            "SELECT 1 FROM model_calls WHERE job_id=? LIMIT 1", (job_id,))).fetchone()
        if row is None:
            await self.release_reservation_in_transaction(job_id, 'work_ended_without_a_model_call')
            return
        await self.settle_reservation_in_transaction(job_id, conservative_output_tokens=conservative_output_tokens)

    async def begin_model_call(self, *, scene_id, episode_id, job_id, batch_id, role, purpose,
                               provider_id, model, reasoning_effort, estimate):
        call_id = uuid.uuid4().hex
        async with self._write_lock:
            await self._db.execute("""INSERT INTO model_calls
                (id,scene_id,episode_id,job_id,batch_id,role,purpose,provider_id,model,reasoning_effort,
                 started_at,status,estimate_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,'unconfirmed',?)""",
                (call_id, scene_id, episode_id, job_id, batch_id, role, purpose, provider_id, model,
                 reasoning_effort, self.clock(), json.dumps(estimate)))
            await self._db.commit()
        return call_id

    async def end_model_call(self, call_id, *, status, usage=None, output_estimate_tokens=None, error_type=None):
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Invalid model call completion status")
        async with self._write_lock:
            cursor = await self._db.execute("""UPDATE model_calls SET ended_at=?,status=?,usage_json=?,
                output_estimate_tokens=?,error_type=? WHERE id=? AND ended_at IS NULL""",
                (self.clock(), status, json.dumps(usage) if usage is not None else None,
                 output_estimate_tokens, error_type, call_id))
            if cursor.rowcount != 1:
                raise ValueError("Model call is missing or already ended")
            await self._db.commit()

    async def set_model_call_disposition(self, episode_id, disposition):
        if disposition not in {"silence", "expression", "rejected"}:
            raise ValueError("Invalid conversation disposition")
        async with self._write_lock:
            await self._db.execute("UPDATE model_calls SET disposition=? WHERE episode_id=? AND role='conversation'",
                                   (disposition, episode_id))
            await self._db.commit()

    async def list_model_calls(self, scene_id=None, limit=100):
        cursor = await self._db.execute("""SELECT * FROM model_calls WHERE (? IS NULL OR scene_id=?)
            ORDER BY started_at DESC,id DESC LIMIT ?""", (scene_id, scene_id, limit))
        names = [column[0] for column in cursor.description]
        result = []
        for row in await cursor.fetchall():
            item = dict(zip(names, row))
            usage = item.pop("usage_json")
            item["usage"] = json.loads(usage) if usage is not None else None
            item["estimate"] = json.loads(item.pop("estimate_json"))
            result.append(item)
        return result

    async def get_model_call_totals(self, scene_id=None):
        # Raw provider usage stays intact above. Derived totals preserve the
        # provider's output meaning; reasoning is never added to completion.
        cursor = await self._db.execute("""SELECT purpose,disposition,status,usage_json,estimate_json
            FROM model_calls WHERE (? IS NULL OR scene_id=?)""", (scene_id, scene_id))
        totals = {}
        for purpose, disposition, status, encoded_usage, encoded_estimate in await cursor.fetchall():
            item = totals.setdefault((purpose, disposition), {"purpose": purpose, "disposition": disposition,
                "calls": 0, "completed": 0, "failed": 0, "cancelled": 0, "unconfirmed": 0,
                "unknown_usage": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0, "estimated_input_tokens": 0})
            item["calls"] += 1
            item[status] += 1
            usage = json.loads(encoded_usage) if encoded_usage is not None else None
            if not isinstance(usage, dict):
                usage = None
            item["estimated_input_tokens"] += json.loads(encoded_estimate)["input_tokens"]
            if not isinstance(usage, dict) or not isinstance(usage.get("prompt_tokens"), (int, float)) or not isinstance(usage.get("completion_tokens"), (int, float)):
                item["unknown_usage"] += 1
            if isinstance(usage, dict):
                for field in ("prompt_tokens", "completion_tokens"):
                    if isinstance(usage.get(field), (int, float)):
                        item[field] += usage[field]
                for field, details, key in (("cached_tokens", "prompt_tokens_details", "cached_tokens"),
                                             ("reasoning_tokens", "completion_tokens_details", "reasoning_tokens")):
                    detail = usage.get(details)
                    value = detail.get(key) if isinstance(detail, dict) else None
                    if isinstance(value, (int, float)):
                        item[field] += value
        return list(totals.values())
