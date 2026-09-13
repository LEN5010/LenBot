"""Durable record of external executions and their ordered events.

An external process's life is not the work's business outcome.  This journal
answers three questions and no others: which execution belongs to which work
revision, what did that execution actually do, and is its container really
gone.  Nothing here decides whether a work is complete — ``exited`` says a
process ended, and the work's own completed/partial/failed result stays in the
work ledger.

One row per execution, keyed by the execution id LenBot assembled.  The id is
the idempotency key: submitting the same execution twice returns the stored
row instead of starting a second container, and submitting the same id with
different content is a caller error rather than a second run.
"""
from __future__ import annotations

import json
from typing import Any

from len_bot.execution.protocol import (
    ExecutionEvent, ExecutionRecord, ExecutionRequest, ExecutionState,
    TerminationReport, is_terminal,
)

# Which state may follow which.  A run that ended on its own is terminal: its
# container's removal is a separate recorded fact, not a change of outcome.
ALLOWED_TRANSITIONS: dict[ExecutionState, frozenset[ExecutionState]] = {
    ExecutionState.ACCEPTED: frozenset({
        ExecutionState.STARTING, ExecutionState.FAILED, ExecutionState.CANCEL_REQUESTED}),
    ExecutionState.STARTING: frozenset({
        ExecutionState.RUNNING, ExecutionState.EXITED, ExecutionState.FAILED,
        ExecutionState.CANCEL_REQUESTED}),
    ExecutionState.RUNNING: frozenset({
        ExecutionState.EXITED, ExecutionState.FAILED, ExecutionState.CANCEL_REQUESTED}),
    ExecutionState.CANCEL_REQUESTED: frozenset({
        ExecutionState.TERMINATION_CONFIRMED, ExecutionState.TERMINATION_UNCONFIRMED}),
    ExecutionState.EXITED: frozenset(),
    ExecutionState.FAILED: frozenset(),
    ExecutionState.TERMINATION_CONFIRMED: frozenset(),
    ExecutionState.TERMINATION_UNCONFIRMED: frozenset(),
}

# What has to agree for a repeated submission to count as the same execution.
# ``deadline_seconds`` is deliberately absent: a resubmission must not be able
# to push the absolute deadline out, and the stored ``deadline_at`` is what a
# later read — and a Gateway restarting on its own — both use.
_RESUBMIT_FIELDS = ('scene_id', 'job_id', 'job_revision', 'workspace_id', 'worker_type',
                    'image_ref', 'network_policy', 'script')

_RECORD_NAMES = ('execution_id', 'scene_id', 'job_id', 'job_revision', 'workspace_id', 'worker_type',
                 'image_ref', 'network_policy', 'state', 'accepted_at', 'deadline_at', 'started_at',
                 'ended_at', 'returncode', 'error', 'termination_json', 'stdout', 'stderr',
                 'stdout_truncated', 'stderr_truncated', 'last_sequence')
_RECORD_COLUMNS = ','.join(_RECORD_NAMES)
_AT = {name: index for index, name in enumerate(_RECORD_NAMES)}


class ExecutionIdentityConflict(ValueError):
    """The same execution id was submitted with different content.

    A distinct type, so a caller can turn this refusal into an answer to the
    caller while an ordinary programming error is not quietly reported as one.
    """


def _decode_execution(row) -> ExecutionRecord | None:
    if row is None:
        return None
    data = dict(zip(_RECORD_NAMES, row))
    # SQLite stores the enum as text and the two flags as 0/1; converting here
    # keeps the stored representation and the typed contract from drifting
    # apart, instead of loosening the record's types to match the columns.
    data['state'] = ExecutionState(data['state'])
    data['stdout_truncated'] = bool(data['stdout_truncated'])
    data['stderr_truncated'] = bool(data['stderr_truncated'])
    data['termination'] = (TerminationReport.model_validate(json.loads(data.pop('termination_json')))
                           if data['termination_json'] else None)
    data.pop('termination_json', None)
    return ExecutionRecord.model_validate(data)


class ExecutionJournalMixin:
    async def initialize_executions(self):
        # One row per execution: the work revision it belongs to, the fixed
        # references it was admitted under, and where it actually got to.  It
        # is not a second task scheduler — there is no polling loop here and no
        # business status column.
        await self._db.execute("""CREATE TABLE IF NOT EXISTS execution_runs (
            execution_id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, job_id TEXT NOT NULL,
            job_revision INTEGER NOT NULL, workspace_id TEXT NOT NULL, worker_type TEXT NOT NULL,
            image_ref TEXT NOT NULL, network_policy TEXT NOT NULL, request_json TEXT NOT NULL,
            state TEXT NOT NULL, accepted_at REAL NOT NULL, deadline_at REAL NOT NULL,
            started_at REAL, ended_at REAL, returncode INTEGER, error TEXT,
            termination_json TEXT, stdout TEXT NOT NULL DEFAULT '', stderr TEXT NOT NULL DEFAULT '',
            stdout_truncated INTEGER NOT NULL DEFAULT 0, stderr_truncated INTEGER NOT NULL DEFAULT 0,
            last_sequence INTEGER NOT NULL DEFAULT 0)""")
        await self._db.execute("""CREATE INDEX IF NOT EXISTS idx_execution_runs_job
            ON execution_runs(scene_id,job_id,job_revision)""")
        # Ordered facts about a run.  A reader resumes from the last sequence
        # it adopted, so re-reading a window cannot apply a fact twice.
        await self._db.execute("""CREATE TABLE IF NOT EXISTS execution_events (
            execution_id TEXT NOT NULL, sequence INTEGER NOT NULL, kind TEXT NOT NULL,
            at REAL NOT NULL, detail TEXT NOT NULL, PRIMARY KEY(execution_id,sequence))""")

    async def record_execution(self, request: ExecutionRequest) -> tuple[ExecutionRecord, bool]:
        """Store one admitted execution; return it and whether it is new.

        The stored row is what a later query reads, so it is written before any
        container is asked to start.  A repeated submission of the same
        execution id returns the existing row unchanged — that is what makes a
        client timeout recoverable instead of a second container, and it is
        also why a retry cannot extend the original deadline.  The same id with
        different content is refused: quietly accepting it would mean the
        stored row no longer describes what would run.
        """
        async with self._write_lock:
            await self._db.execute('BEGIN IMMEDIATE')
            try:
                existing = await self._execution_row(request.execution_id)
                if existing is not None:
                    stored = json.loads(existing[-1])
                    mismatch = [field for field in _RESUBMIT_FIELDS
                                if stored.get(field) != getattr(request, field)]
                    if stored.get('initiator') != request.initiator.model_dump(mode='json'):
                        mismatch.append('initiator')
                    if stored.get('input_assets') != request.input_assets:
                        mismatch.append('input_assets')
                    if mismatch:
                        raise ExecutionIdentityConflict(
                            f'执行身份 {request.execution_id} 已存在且内容不同（{",".join(mismatch)}）；'
                            '同一执行 ID 不重复启动，需要新执行的请使用新的 ID')
                    await self._db.commit()
                    return _decode_execution(existing[:len(_RECORD_NAMES)]), False
                accepted_at = self.clock()
                await self._db.execute("""INSERT INTO execution_runs
                    (execution_id,scene_id,job_id,job_revision,workspace_id,worker_type,image_ref,
                     network_policy,request_json,state,accepted_at,deadline_at,last_sequence)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                    (request.execution_id, request.scene_id, request.job_id, request.job_revision,
                     request.workspace_id, request.worker_type, request.image_ref,
                     request.network_policy, request.model_dump_json(),
                     ExecutionState.ACCEPTED.value, accepted_at,
                     accepted_at + request.deadline_seconds))
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        record = await self.get_execution(request.execution_id)
        if record is None:
            raise RuntimeError('执行已写入但无法读回；保持停机并核对执行日志')
        return record, True

    async def _execution_row(self, execution_id: str):
        return await (await self._db.execute(
            f"SELECT {_RECORD_COLUMNS},request_json FROM execution_runs WHERE execution_id=?",
            (execution_id,))).fetchone()

    async def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        row = await self._execution_row(execution_id)
        if row is None:
            return None
        return _decode_execution(row[:len(_RECORD_NAMES)])

    async def execution_request_of(self, execution_id: str) -> ExecutionRequest | None:
        """The admitted request itself, for callers that must re-check identity."""
        row = await self._execution_row(execution_id)
        if row is None:
            return None
        return ExecutionRequest.model_validate_json(row[-1])

    async def executions_for_job(self, scene_id: str, job_id: str) -> list[ExecutionRecord]:
        rows = await (await self._db.execute(
            f"SELECT {_RECORD_COLUMNS} FROM execution_runs WHERE scene_id=? AND job_id=?"
            " ORDER BY job_revision,accepted_at", (scene_id, job_id))).fetchall()
        return [record for record in map(_decode_execution, rows) if record is not None]

    async def append_execution_event(self, execution_id: str, kind: str, detail: str = '',
                                     *, state: ExecutionState | None = None,
                                     error: str | None = None,
                                     returncode: int | None = None,
                                     termination: TerminationReport | None = None,
                                     stdout: str | None = None, stderr: str | None = None,
                                     stdout_truncated: bool | None = None,
                                     stderr_truncated: bool | None = None,
                                     ) -> ExecutionEvent:
        """Record one fact about a run, optionally moving its state with it.

        The state change and the fact that explains it are one transaction: a
        run is never left in a new state without the ordered event saying why,
        and a reader replaying events sees the same order a live observer did.
        A refusal here (an impossible transition) leaves both untouched.
        """
        async with self._write_lock:
            await self._db.execute('BEGIN IMMEDIATE')
            try:
                row = await self._execution_row(execution_id)
                if row is None:
                    raise ValueError(f'执行 {execution_id} 不在执行日志中')
                current = ExecutionState(row[_AT['state']])
                if state is not None and state != current and state not in ALLOWED_TRANSITIONS[current]:
                    raise ValueError(f'执行状态不能从 {current.value} 变成 {state.value}')
                sequence = int(row[_AT['last_sequence']]) + 1
                at = self.clock()
                await self._db.execute(
                    "INSERT INTO execution_events(execution_id,sequence,kind,at,detail) VALUES(?,?,?,?,?)",
                    (execution_id, sequence, kind, at, detail[:2000]))
                updates, values = ["last_sequence=?"], [sequence]
                if state is not None:
                    updates.append("state=?")
                    values.append(state.value)
                    if state is ExecutionState.STARTING and row[_AT['started_at']] is None:
                        updates.append("started_at=?")
                        values.append(at)
                    if is_terminal(state):
                        updates.append("ended_at=?")
                        values.append(at)
                    if returncode is not None or state in {ExecutionState.EXITED, ExecutionState.FAILED}:
                        updates.append("returncode=?")
                        values.append(returncode)
                    if error is not None or state is ExecutionState.FAILED:
                        updates.append("error=?")
                        values.append(error)
                    if termination is not None:
                        updates.append("termination_json=?")
                        values.append(termination.model_dump_json())
                for column, value in (('stdout', stdout), ('stderr', stderr),
                                      ('stdout_truncated', stdout_truncated),
                                      ('stderr_truncated', stderr_truncated)):
                    if value is not None:
                        updates.append(f"{column}=?")
                        values.append(int(value) if isinstance(value, bool) else value)
                values.append(execution_id)
                await self._db.execute(
                    f"UPDATE execution_runs SET {','.join(updates)} WHERE execution_id=?", values)
                await self._db.commit()
            except BaseException:
                await self._db.rollback()
                raise
        return ExecutionEvent(sequence=sequence, kind=kind, at=at, detail=detail[:2000])

    async def read_execution_events(self, execution_id: str, after: int = 0) -> list[ExecutionEvent]:
        """Events with a sequence above ``after``, in the order they happened."""
        rows = await (await self._db.execute(
            "SELECT sequence,kind,at,detail FROM execution_events WHERE execution_id=? AND sequence>?"
            " ORDER BY sequence", (execution_id, after))).fetchall()
        return [ExecutionEvent(sequence=int(row[0]), kind=row[1], at=float(row[2]), detail=row[3])
                for row in rows]

    def execution_view(self, record: ExecutionRecord) -> dict[str, Any]:
        """The record as the panel and the client read it; technical state only."""
        return record.model_dump(mode='json')
