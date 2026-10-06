"""Read the tokens services actually reported, from each call's owner, grouped without a ledger.

Transcription and embeddings are listed but do not count toward the daily token
limit: they are metered differently and local services often report nothing.
"""
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from .tokens import token_summary
from ..memory.jobs import processing_records


UNBUDGETED_ROLES = frozenset({"asr", "expression_embedding", "memory_embedding"})


@dataclass(frozen=True)
class UsageCall:
    scene: str
    role: str
    started: float
    ended: float | None
    tokens: dict | None
    error: str | None


def call_records(store, scenes: list[str] | None, since: float, until: float, *, memory_db=None) -> list[UsageCall]:
    """Read the existing call owners once, retaining failures and actual start times."""
    calls = []
    def collect(rows):
        for scene, role, started, ended, tokens, error in rows:
            calls.append(UsageCall(scene, role, started, ended,
                                   None if tokens is None else json.loads(tokens), error))
    names = '' if scenes is None else ','.join('?' for _ in scenes)
    condition = '1=1' if scenes is None else f'scene IN ({names})'
    params = (since, until) if scenes is None else (*scenes, since, until)
    collect(store.db.execute(
        "SELECT c.scene,CASE WHEN c.plugin IS NULL THEN c.role ELSE 'plugin:' || c.plugin END,c.started,c.ended,c.tokens,c.error "
        "FROM model_calls c "
        f"WHERE {condition if scenes is None else 'c.'+condition} AND c.started>=? AND c.started<?", params))
    for table, role, start, tokens in (
        ('audio_calls', 'asr', 'started', 'tokens'),
        ('learning_batches', 'learning', 'model_started', 'tokens'),
        ('expression_embedding_calls', 'expression_embedding', 'started', 'tokens'),
        ('jargon_calls', 'jargon', 'model_started', 'tokens'),
        ('sticker_calls', 'sticker', 'model_started', 'tokens'),
        ('reply_effect_calls', 'reply_effects', 'model_started', 'tokens'),
    ):
        collect(store.db.execute(f"SELECT scene,?,{start},ended,{tokens},error FROM {table} WHERE {condition} "
                                 f"AND {start}>=? AND {start}<?", (role, *params)))
    collect(store.db.execute(
        "SELECT scene,'worker',created,json_extract(body,'$.ended'),json_extract(body,'$.response.tokens'),"
        "json_extract(body,'$.response.error') "
        f"FROM task_events WHERE {condition} AND kind='model_call' AND created>=? AND created<?", params))
    if memory_db is not None:
        db = memory_db
        collect(db.execute(f"SELECT scene,'memory_summary',model_started,ended,tokens,error FROM memory_summary_runs "
                           f"WHERE {condition} AND model_started>=? AND model_started<?", params))
        collect(db.execute(
            "SELECT j.scene,'memory',json_extract(c.value,'$.started'),json_extract(c.value,'$.ended'),"
            "json_extract(c.value,'$.tokens'),json_extract(c.value,'$.error') "
            "FROM memory_jobs j,json_each(j.details,'$.calls') c "
            f"WHERE {condition if scenes is None else 'j.'+condition} AND json_extract(c.value,'$.started')>=? "
            "AND json_extract(c.value,'$.started')<?", params))
        collect(db.execute(f"SELECT scene,'memory_embedding',started,ended,tokens,error FROM memory_embedding_calls "
                           f"WHERE {condition} AND started>=? AND started<?", params))
    return calls


def instance_calls(store, database: Path, scenes: list[str] | None, since: float, until: float, *,
                   memory=None, trials_root: Path | None = None) -> list[UsageCall]:
    """The same main, memory and retained-trial sources used by the daily allowance."""
    with processing_records(database, None if memory is None else memory.jobs) as records:
        calls = call_records(store, scenes, since, until, memory_db=None if records is None else records.db)
    if trials_root is not None:
        for path in trials_root.glob('*/state.db'):
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                with processing_records(path, None) as records:
                    calls.extend(call_records(SimpleNamespace(db=db), scenes, since, until,
                                              memory_db=None if records is None else records.db))
    return calls


def summarize_calls(calls: list[UsageCall], since: float, until: float) -> dict:
    grouped = defaultdict(list)
    for call in calls:
        grouped[call.scene, call.role].append(call.tokens)
    budgeted_calls = [call for call in calls if call.role not in UNBUDGETED_ROLES]
    budgeted = [call.tokens for call in budgeted_calls]
    every = [call.tokens for call in calls]
    return {"since": since, "until": until, "calls": len(every),
            "unfinished_calls": sum(call.ended is None for call in budgeted_calls),
            "settled_unknown_calls": sum(call.ended is not None and call.tokens is None for call in budgeted_calls),
            "successful_unknown_calls": sum(call.ended is not None and call.error is None and call.tokens is None
                                            for call in budgeted_calls),
            "budgeted": token_summary(budgeted), **token_summary(every),
            "groups": [{"scene": scene, "role": role, "calls": len(token_records), **token_summary(token_records)}
                       for (scene, role), token_records in sorted(grouped.items())]}


def usage(store, scenes: list[str] | None, since: float, until: float, *, memory=None, memory_db=None) -> dict:
    db = memory.jobs.db if memory is not None else memory_db
    result = summarize_calls(call_records(store, scenes, since, until, memory_db=db), since, until)
    unmetered = []
    unverified_summaries = 0
    if db is not None:
        condition = '1=1' if scenes is None else f"scene IN ({','.join('?' for _ in scenes)})"
        params = (until, since) if scenes is None else (*scenes, until, since)
        unverified_summaries = db.execute(
            f"SELECT COUNT(*) FROM memory_summary_runs WHERE {condition} AND model_started IS NULL "
            "AND started<? AND (ended IS NULL OR ended>=?)", params).fetchone()[0]
        if unverified_summaries:
            unmetered.append(f"{unverified_summaries} 次历史摘要尝试没有实际模型开始时刻")
    return {**result, "unverified_summary_attempts": unverified_summaries, "unmetered_sources": unmetered}
