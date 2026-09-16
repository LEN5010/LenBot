"""Durable accounting for actual provider requests, without a billing claim.

Two absences are kept apart throughout this module, because the plan treats
them as different facts and a caller that conflates them would either hand out
unnumbered execution or refuse a work the operator deliberately left open:

* ``NULL`` in a *recorded* ceiling column means the operator configured no
  token dimension for that work — a real choice, and the work's deadline and
  message limits are then its whole stopping condition;
* *no record at all* means the work predates the snapshot, so its ceiling was
  never written down and cannot be reconstructed from its own rows.  That is
  read as "unknown", refused by admission, and never quietly turned into
  unlimited.
"""

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
    stands in, and its output is bounded by that request's own recorded
    output ceiling.  The two totals stay separable so no view can call an
    estimate real billing.  A half-reported usage is not split between the
    two: an input without its matching output cannot be attributed, so that
    call is kept as an estimate rather than guessed at.
    """
    if isinstance(usage, dict):
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if (isinstance(prompt, (int, float)) and not isinstance(prompt, bool)
                and isinstance(completion, (int, float)) and not isinstance(completion, bool)):
            # prompt_tokens + completion_tokens is the whole metered exchange.
            return max(0, int(prompt) + int(completion)), 0
    estimated = int(estimate.get("input_tokens") or 0) + max(0, int(conservative_output_tokens))
    return 0, max(0, estimated)


def call_output_bound(row) -> int:
    """This request's own output bound, for a call that never reported usage.

    The number is read from the call itself rather than from the running
    configuration: the output an already-finished request was allowed to
    produce is a fact about that request, and a later policy edit must not
    rewrite what past calls are counted as.  A completed call's actual
    continuation length is the closest thing to its real output; a call that
    ended without one keeps the ceiling it was admitted under.  Only a row
    that predates both columns falls back to the caller's current value.
    """
    for value in (row.get('output_estimate_tokens'), row.get('output_ceiling_tokens')):
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return 0


class ModelCallStoreMixin:
    async def initialize_model_calls(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS model_calls (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, episode_id TEXT, job_id TEXT, batch_id TEXT,
            role TEXT NOT NULL, purpose TEXT NOT NULL, provider_id TEXT NOT NULL, model TEXT NOT NULL,
            reasoning_effort TEXT, started_at REAL NOT NULL, ended_at REAL,
            status TEXT NOT NULL, usage_json TEXT, estimate_json TEXT NOT NULL,
            output_estimate_tokens INTEGER, error_type TEXT, disposition TEXT)""")
        # Two facts a call has to carry once its own budget is admitted: the
        # output ceiling it was admitted under (so the split a later settle
        # does is this call's own, not today's configuration) and the tokens
        # it holds in flight (so the next call can see that they are spoken
        # for).  Both are added in place on an existing ledger.
        columns = {column[1] for column in await (await self._db.execute("PRAGMA table_info(model_calls)")).fetchall()}
        for name, declaration in (('output_ceiling_tokens', 'INTEGER'), ('held_tokens', 'INTEGER')):
            if name not in columns:
                await self._db.execute(f'ALTER TABLE model_calls ADD COLUMN {name} {declaration}')
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
        # The ceiling the work was accepted under, kept beside what it has
        # consumed.  Settling overwrites `reserved_tokens` with the spend, so
        # without this column the original allowance is gone the moment a work
        # finishes; a continuation would then be re-measured against today's
        # policy instead of the number it was actually granted.
        reservation_columns = {column[1] for column in await (await self._db.execute(
            "PRAGMA table_info(usage_reservations)")).fetchall()}
        if 'limit_tokens' not in reservation_columns:
            await self._db.execute('ALTER TABLE usage_reservations ADD COLUMN limit_tokens INTEGER')
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_usage_reservations_day ON usage_reservations(subject,day_key)")
        # A hold that already existed when this column was added still knows
        # its ceiling: for a row that has never been settled, `reserved_tokens`
        # is still that number.  Filling it once is restoring the fact that was
        # there all along, not inventing one.  A row that was already settled
        # before the column existed cannot be reconstructed from its own rows
        # and is left null, which reads as "this work has no recorded ceiling"
        # and is refused by the re-hold instead of being silently re-created
        # from the current default policy.
        await self._db.execute("""UPDATE usage_reservations SET limit_tokens=reserved_tokens
            WHERE limit_tokens IS NULL AND status='held' AND reserved_tokens > 0
            AND NOT EXISTS (SELECT 1 FROM agent_jobs j WHERE j.id=usage_reservations.job_id
                AND json_type(j.budget_json,'$.token_limit') IS NOT NULL)""")
        # A request that was in flight when the process last stopped has no
        # receipt and can never get one: nothing will call end_model_call for
        # it.  Left open, it keeps its work settling forever and its hold
        # counted against the day.  It is closed here as failed with what is
        # known, and every work that was only waiting on such a request
        # settles on its real receipts — unless an admitted skill candidate
        # still has to run under the same hold.
        await self._db.execute("""UPDATE model_calls SET ended_at=?,status='failed',held_tokens=NULL,
            error_type='process_restart' WHERE ended_at IS NULL""", (self.clock(),))
        settling = await (await self._db.execute(
            "SELECT job_id FROM usage_reservations WHERE status='settling'")).fetchall()
        for (job_id,) in settling:
            await self._settle_if_open_in_transaction(job_id)

    async def account_used_tokens_in_transaction(self, subject: str, day_key: str, *,
                                                 scene_id: str | None = None,
                                                 exclude_job_id: str | None = None) -> int:
        """Held plus already-settled tokens for one account on one day.

        `settling` counts as held: a work whose business result is committed
        but whose requests are still unanswered is still occupying the day it
        was accepted on, and reading it as free would let the next work spend
        the same tokens.  `exclude_job_id` leaves one work out of the total —
        a work being put back on hold is re-checked against the same day, and
        its own earlier settlement is exactly what the new hold replaces;
        counting both would charge one work twice.
        """
        column, value = ("subject", subject) if scene_id is None else ("scene_id", scene_id)
        row = await (await self._db.execute(
            f"""SELECT COALESCE(SUM(CASE WHEN status IN ('held','settling') THEN reserved_tokens
                    ELSE COALESCE(usage_tokens,0)+COALESCE(estimated_tokens,0) END),0)
                FROM usage_reservations WHERE {column}=? AND day_key=?
                AND status IN ('held','settling','settled')
                AND (? IS NULL OR job_id!=?)""",
            (value, day_key, exclude_job_id, exclude_job_id))).fetchone()
        return int(row[0] or 0)

    async def reserve_work_in_transaction(self, *, job_id, scene_id, subject, day_key, tokens,
                                          limit_tokens=None, policy_name=None, scene_limit=None, daily_limit=None):
        """Hold one work's budget inside the caller's existing write transaction.

        `limit_tokens` is the work's cumulative ceiling, which is not always
        the same number as this hold: a work with no token dimension holds
        nothing but still records a null ceiling, and a work re-held after a
        partial spend holds only its remainder.  When the caller does not
        distinguish the two, the hold is the ceiling.

        The caller must say which of the two it means, because the caller is
        the only side that knows: a caller that omits the ceiling is recorded
        with the one that applies.  Saying nothing and meaning "no ceiling" is
        expressible as `limit_tokens=None` together with `tokens=0`; the
        default of "the hold is the ceiling" is what keeps an old caller from
        writing a row whose two columns disagree.
        """
        if tokens < 0:
            raise ValueError('A work reservation cannot be negative')
        # tokens=0 with omitted ceiling is the recorded "no token dimension".
        # Substituting tokens into limit_tokens would write 0 and later read
        # as a real ceiling of nothing.
        if limit_tokens is None:
            ceiling = None if tokens == 0 else tokens
        else:
            ceiling = limit_tokens
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
            (job_id,scene_id,subject,day_key,policy_name,reserved_tokens,limit_tokens,status,created_at)
            VALUES(?,?,?,?,?,?,?,'held',?)""",
            (job_id, scene_id, subject, day_key, policy_name, tokens, ceiling, self.clock()))

    async def rehold_work_in_transaction(self, *, job_id, scene_limit=None, daily_limit=None):
        """Put an ended work back on hold under the ceiling it was created with.

        The hold is the recorded `limit_tokens`, not today's policy and not
        the last settlement amount.  The day is charged for that ceiling in
        place of this work's earlier settlement, so the same allowance is not
        counted twice and unused remainder is taken back from the day.
        """
        current = await (await self._db.execute(
            "SELECT scene_id,subject,day_key,status,limit_tokens FROM usage_reservations WHERE job_id=?",
            (job_id,))).fetchone()
        if current is None:
            raise ValueError('This work has no reservation to re-hold')
        scene_id, subject, day_key, status, limit_tokens = current
        if status in {'held', 'settling'}:
            return
        if status not in {'settled', 'released'}:
            raise ValueError('This work has no settlement to re-hold')
        recorded, original_ceiling = await self.recorded_work_ceiling(job_id)
        if not recorded:
            raise ValueError(
                '该工作没有记录创建时的累计上限（早于预占上限存档，或已被结算覆盖），'
                '不能按当前默认策略补造一个额度；请保留原记录并交由运营者决定是否另立工作')
        if original_ceiling is None:
            if daily_limit is not None or scene_limit is not None:
                raise ValueError('该工作明确不设累计 token 上限，当前有限日额度无法为其预占；不能补造上限或按零预占绕过')
            # Preserve already measured usage; a zero hold must not erase the
            # released work's consumption from account totals while running.
            actual, estimated = await self.job_measured_tokens(job_id)
            await self._db.execute("""UPDATE usage_reservations SET reserved_tokens=?,
                status='held',settled_at=NULL WHERE job_id=? AND status IN ('settled','released')""",
                (actual + estimated, job_id))
            return
        ceiling = original_ceiling
        if daily_limit is not None:
            used = await self.account_used_tokens_in_transaction(subject, day_key, exclude_job_id=job_id)
            if used + ceiling > daily_limit:
                raise ValueError(
                    f'每日额度不足：该账号 {day_key} 已占用 {used}（不含本工作），本工作要继续还需预占 {ceiling}，'
                    f'上限 {daily_limit}；继续旧工作不新增额度，需要更多额度请由运营者明确调整策略或另立一个范围更小的工作')
        if scene_limit is not None:
            used = await self.account_used_tokens_in_transaction(subject, day_key, scene_id=scene_id, exclude_job_id=job_id)
            if used + ceiling > scene_limit:
                raise ValueError(
                    f'本群额度不足：{scene_id} 在 {day_key} 已占用 {used}（不含本工作），本工作要继续还需预占 {ceiling}，'
                    f'上限 {scene_limit}')
        await self._db.execute("""UPDATE usage_reservations SET reserved_tokens=?,status='held',settled_at=NULL
            WHERE job_id=? AND status IN ('settled','released')""", (ceiling, job_id))

    async def release_reservation_in_transaction(self, job_id: str, reason: str = ''):
        """Give back a hold whose work never consumed anything."""
        await self._db.execute("""UPDATE usage_reservations SET status='released',settled_at=?
            WHERE job_id=? AND status='held'""", (self.clock(), job_id))

    async def close_reservation_in_transaction(self, job_id, *, conservative_output_tokens: int):
        """End a hold: release it free, or settle it if the work already billed.

        A work cancelled before its first model call gives the whole hold back.
        One that already called a provider keeps those real tokens on its
        account, so cancelling is never a way to erase spent budget.

        A work may still have requests in flight when this is reached, and a
        cancelled or interrupted request cannot be proven to have stopped
        billing.  A pending skill candidate is admitted future consumption of
        the same hold.  Either way the work is left settling: its hold stays,
        the in-flight amount keeps counting, and the last request to end —
        after the last candidate is resolved — closes the account with its
        real receipt.  Settling early would return remainder to the day that
        maintenance then takes back without any admission check.
        """
        pending_maintenance = await self._pending_skill_candidates_in_transaction(job_id)
        row = await (await self._db.execute(
            "SELECT 1 FROM model_calls WHERE job_id=? LIMIT 1", (job_id,))).fetchone()
        if row is None and not pending_maintenance:
            await self.release_reservation_in_transaction(job_id, 'work_ended_without_a_model_call')
            return
        if await self.in_flight_model_calls(job_id) > 0 or pending_maintenance:
            await self._db.execute(
                """UPDATE usage_reservations SET status='settling' WHERE job_id=? AND status='held'""",
                (job_id,))
            return
        await self.settle_reservation_in_transaction(job_id, conservative_output_tokens=conservative_output_tokens)

    async def in_flight_model_calls(self, job_id: str) -> int:
        """How many of this work's requests have not yet recorded an ending."""
        row = await (await self._db.execute(
            "SELECT COUNT(*) FROM model_calls WHERE job_id=? AND ended_at IS NULL", (job_id,))).fetchone()
        return int(row[0] or 0) if row else 0

    async def work_budget_facts(self, job_id: str) -> tuple[int | None, float | None]:
        """This work's recorded token ceiling and absolute deadline, if any.

        Two different absences must not read the same.  A work whose snapshot
        records `token_limit: null` was created with *no token dimension at
        all* — the operator's own choice, and its deadline and message limits
        are its whole stopping condition.  A work that predates the snapshot
        has no recorded ceiling either, but nothing about it says that; reading
        that as unlimited would let it spend without bound on a day whose
        balance it never counted against.  Since this reader cannot tell those
        apart from the work row alone, it answers conservatively and lets the
        caller decide: the reservation is the other durable fact, and a work
        that has one knows its ceiling from `limit_tokens`.
        """
        row = await (await self._db.execute(
            "SELECT budget_json FROM agent_jobs WHERE id=?", (job_id,))).fetchone()
        if row is None or not row[0]:
            return None, None
        data = json.loads(row[0])
        token_limit, deadline_at = data.get('token_limit'), data.get('deadline_at')
        ceiling = (int(token_limit) if isinstance(token_limit, (int, float))
                   and not isinstance(token_limit, bool) else None)
        deadline = (float(deadline_at) if isinstance(deadline_at, (int, float))
                    and not isinstance(deadline_at, bool) else None)
        return ceiling, deadline

    async def recorded_work_ceiling(self, job_id: str) -> tuple[bool, int | None]:
        """The work's ceiling as a recorded-or-not answer, from both durable facts.

        Returns `(recorded, ceiling)`.  `recorded` false means no ceiling was
        ever established for this work; that is a refusal, not "unlimited".
        A recorded `None` is the operator's explicit no-token-dimension choice,
        taken from the snapshot when it says so, or from the reservation's
        `limit_tokens` for a work whose snapshot predates the column.
        """
        row = await (await self._db.execute(
            "SELECT budget_json FROM agent_jobs WHERE id=?", (job_id,))).fetchone()
        if row is not None and row[0]:
            data = json.loads(row[0])
            if 'token_limit' in data:
                limit = data['token_limit']
                if limit is None:
                    return True, None
                if isinstance(limit, (int, float)) and not isinstance(limit, bool):
                    return True, int(limit)
        reservation = await (await self._db.execute(
            "SELECT limit_tokens FROM usage_reservations WHERE job_id=?", (job_id,))).fetchone()
        if reservation is not None and reservation[0] is not None:
            return True, int(reservation[0])
        return False, None

    async def job_measured_tokens(self, job_id: str, *, conservative_output_tokens: int = 0) -> tuple[int, int]:
        """What this work has spent so far, split into real usage and estimate.

        Ended calls use their own recorded output bound.  A request still in
        flight counts at the tokens it was admitted to hold, so the next
        admission can see them.  Rows that predate those columns fall back to
        the caller's current conservative output, which is only a last resort.
        """
        rows = await (await self._db.execute(
            """SELECT usage_json,estimate_json,output_estimate_tokens,output_ceiling_tokens,held_tokens,ended_at
               FROM model_calls WHERE job_id=?""", (job_id,))).fetchall()
        usage_tokens = estimated_tokens = 0
        for encoded_usage, encoded_estimate, output_estimate, output_ceiling, held, ended_at in rows:
            estimate = json.loads(encoded_estimate) if encoded_estimate else {}
            bound = call_output_bound({
                'output_estimate_tokens': output_estimate, 'output_ceiling_tokens': output_ceiling,
            }) or conservative_output_tokens
            if ended_at is None:
                if held is not None:
                    estimated_tokens += max(0, int(held))
                else:
                    estimated_tokens += measured_call_tokens(
                        None, estimate, conservative_output_tokens=bound)[1]
                continue
            usage = json.loads(encoded_usage) if encoded_usage else None
            measured, estimated = measured_call_tokens(
                usage, estimate, conservative_output_tokens=bound)
            usage_tokens += measured
            estimated_tokens += estimated
        return usage_tokens, estimated_tokens

    async def account_reservation_totals(self, day_key: str, *, scene_id=None, subject=None):
        """Full-day held and settled totals per billing subject, not a truncated list."""
        clause, params = "day_key=?", [day_key]
        if scene_id is not None:
            clause += " AND scene_id=?"; params.append(scene_id)
        if subject is not None:
            clause += " AND subject=?"; params.append(subject)
        rows = await (await self._db.execute(
            f"""SELECT subject,
                    COALESCE(SUM(CASE WHEN status IN ('held','settling') THEN reserved_tokens ELSE 0 END),0),
                    COALESCE(SUM(CASE WHEN status='settled'
                        THEN COALESCE(usage_tokens,0)+COALESCE(estimated_tokens,0) ELSE 0 END),0)
                FROM usage_reservations WHERE {clause}
                    AND status IN ('held','settling','settled')
                GROUP BY subject ORDER BY subject""", params)).fetchall()
        return [{'subject': row[0], 'held': int(row[1] or 0), 'used': int(row[2] or 0)} for row in rows]

    async def begin_model_call(self, *, scene_id, episode_id, job_id, batch_id, role, purpose,
                               provider_id, model, reasoning_effort, estimate, output_tokens=0,
                               admission=None):
        """Register one outbound request and hold what it may spend.

        `admission` runs inside this write transaction, before the call row
        exists: it is what decides whether this request may be sent at all,
        and what it holds if it may.  Running it here rather than before the
        transaction is what makes two concurrent requests for one work
        serialise — the first one's hold is already written when the second
        one reads the account.  A refusal rolls the transaction back, so a
        request that was not admitted leaves no call row and no charge behind.
        A call with no work account behind it (a conversation, a probe) passes
        no admission and holds nothing.
        """
        call_id = uuid.uuid4().hex
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                held = None if admission is None else await admission()
                await self._db.execute("""INSERT INTO model_calls
                    (id,scene_id,episode_id,job_id,batch_id,role,purpose,provider_id,model,reasoning_effort,
                     started_at,status,estimate_json,output_ceiling_tokens,held_tokens)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,'unconfirmed',?,?,?)""",
                    (call_id, scene_id, episode_id, job_id, batch_id, role, purpose, provider_id, model,
                     reasoning_effort, self.clock(), json.dumps(estimate), max(0, int(output_tokens)),
                     None if held is None else max(0, int(held))))
                if job_id:
                    await self._reopen_settled_reservation_in_transaction(job_id)
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return call_id

    async def end_model_call(self, call_id, *, status, usage=None, output_estimate_tokens=None, error_type=None):
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Invalid model call completion status")
        async with self._write_lock:
            try:
                await self._db.execute('BEGIN IMMEDIATE')
                cursor = await self._db.execute("""UPDATE model_calls SET ended_at=?,status=?,usage_json=?,
                    output_estimate_tokens=?,held_tokens=NULL,error_type=? WHERE id=? AND ended_at IS NULL""",
                    (self.clock(), status, json.dumps(usage) if usage is not None else None,
                     output_estimate_tokens, error_type, call_id))
                if cursor.rowcount != 1:
                    raise ValueError("Model call is missing or already ended")
                # This request's receipt may be the last one a work that was
                # already ended is waiting for.  The check is keyed by the
                # work and does nothing unless its account is still settling,
                # so it closes exactly once however many requests end.
                row = await (await self._db.execute(
                    "SELECT job_id FROM model_calls WHERE id=?", (call_id,))).fetchone()
                if row and row[0] and await self.in_flight_model_calls(row[0]) == 0:
                    await self._settle_if_open_in_transaction(row[0])
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise

    async def _reopen_settled_reservation_in_transaction(self, job_id: str) -> None:
        """Refuse a new charge against an account that already settled.

        While admitted follow-ups remain — in-flight requests or pending skill
        candidates — the account is kept settling and never reaches this
        state.  A settled account therefore has no admitted consumption left,
        and re-holding its old ceiling here would take back tokens the day may
        have already granted to other works, without any daily-ledger check.
        A resume or revision re-holds through the admission-checked path.
        """
        row = await (await self._db.execute(
            "SELECT status FROM usage_reservations WHERE job_id=?",
            (job_id,))).fetchone()
        if row is None or row[0] != 'settled':
            return
        raise ValueError(
            '该工作的预占已结算，没有获准的后续消费；不能未经日额核对重新占用已退回的额度。'
            '恢复或修订工作请走带日账检查的重占流程。')

    async def _pending_skill_candidates_in_transaction(self, job_id: str) -> bool:
        """Whether this work still has an admitted skill candidate waiting to run."""
        row = await (await self._db.execute(
            "SELECT 1 FROM skill_candidates WHERE job_id=? AND status='pending' LIMIT 1",
            (job_id,))).fetchone()
        return row is not None

    async def _settle_if_open_in_transaction(self, job_id: str) -> bool:
        if await self._pending_skill_candidates_in_transaction(job_id):
            return False
        row = await (await self._db.execute(
            "SELECT status FROM usage_reservations WHERE job_id=?", (job_id,))).fetchone()
        if row is None or row[0] not in {'settling', 'settled'}:
            return False
        await self.settle_reservation_in_transaction(job_id, conservative_output_tokens=0)
        return True

    async def settle_reservation_in_transaction(self, job_id, *, conservative_output_tokens: int):
        """Replace a hold with what the work actually spent, keeping the split.

        Every model call carrying this job id counts here, including
        compression, skill maintenance and plugin sub-agents, so one work has
        one account.  A provider that returned no usage is recorded from that
        call's own estimate and output ceiling instead of being charged as
        zero, and the work's recorded ceiling is left untouched so a later
        continuation still knows what it was granted.

        Idempotent by construction: the totals are a function of the current
        call rows, so running this again after a late receipt recomputes the
        same account from the same facts rather than adding anything twice.
        A settle that lands while a request is still in flight leaves that
        request's own hold in place inside the total.

        A work whose account was already settled is re-settled rather than
        skipped: skill maintenance spends the founding work's allowance after
        its business result is stored, and the day's account has to show those
        tokens too.  A released hold is not reopened — that work never spent
        anything and has no account to refresh.
        """
        usage_tokens, estimated_tokens = await self.job_measured_tokens(
            job_id, conservative_output_tokens=conservative_output_tokens)
        await self._db.execute("""UPDATE usage_reservations SET status='settled',usage_tokens=?,
            estimated_tokens=?,reserved_tokens=?,settled_at=? WHERE job_id=?
            AND status IN ('held','settling','settled')""",
            (usage_tokens, estimated_tokens, usage_tokens + estimated_tokens, self.clock(), job_id))



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
