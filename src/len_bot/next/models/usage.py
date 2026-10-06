"""Read the tokens services actually reported, from each call's owner, grouped without a ledger.

Transcription and embeddings are listed but do not count toward the daily token
limit: they are metered differently and local services often report nothing.
"""
from collections import defaultdict
import json
from .tokens import token_summary


UNBUDGETED_ROLES = frozenset({"asr", "expression_embedding", "memory_embedding"})


def usage(store, scenes: list[str] | None, since: float, until: float, *, memory=None, memory_db=None) -> dict:
    grouped = defaultdict(list)
    unfinished = 0
    settled_unknown = 0
    def collect(rows):
        nonlocal unfinished, settled_unknown
        for scene, role, ended, tokens in rows:
            grouped[scene, role].append(None if tokens is None else json.loads(tokens))
            if role in UNBUDGETED_ROLES:
                continue
            unfinished += ended is None
            settled_unknown += ended is not None and tokens is None
    names = '' if scenes is None else ','.join('?' for _ in scenes)
    condition = '1=1' if scenes is None else f'scene IN ({names})'
    params = (since, until) if scenes is None else (*scenes, since, until)
    collect(store.db.execute(
        "SELECT c.scene,CASE WHEN c.plugin IS NULL THEN c.role ELSE 'plugin:' || c.plugin END,c.ended,c.tokens "
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
        collect(store.db.execute(f"SELECT scene,?,ended,{tokens} FROM {table} WHERE {condition} "
                                 f"AND {start}>=? AND {start}<?", (role, *params)))
    collect(store.db.execute(
        "SELECT scene,'worker',json_extract(body,'$.ended'),json_extract(body,'$.response.tokens') "
        f"FROM task_events WHERE {condition} AND kind='model_call' AND created>=? AND created<?", params))
    unmetered = []
    unverified_summaries = 0
    if memory is not None or memory_db is not None:
        db = memory.jobs.db if memory is not None else memory_db
        legacy_params = (until, since) if scenes is None else (*scenes, until, since)
        unverified_summaries = db.execute(
            f"SELECT COUNT(*) FROM memory_summary_runs WHERE {condition} AND model_started IS NULL "
            "AND started<? AND (ended IS NULL OR ended>=?)", legacy_params).fetchone()[0]
        if unverified_summaries:
            unmetered.append(f"{unverified_summaries} 次历史摘要尝试没有实际模型开始时刻")
        collect(db.execute(f"SELECT scene,'memory_summary',ended,tokens FROM memory_summary_runs "
                           f"WHERE {condition} AND model_started>=? AND model_started<?", params))
        collect(db.execute(
            "SELECT j.scene,'memory',json_extract(c.value,'$.ended'),json_extract(c.value,'$.tokens') "
            "FROM memory_jobs j,json_each(j.details,'$.calls') c "
            f"WHERE {condition if scenes is None else 'j.'+condition} AND json_extract(c.value,'$.started')>=? "
            "AND json_extract(c.value,'$.started')<?", params))
        collect(db.execute(f"SELECT scene,'memory_embedding',ended,tokens FROM memory_embedding_calls "
                           f"WHERE {condition} AND started>=? AND started<?", params))

    budgeted = [record for (_, role), values in grouped.items() if role not in UNBUDGETED_ROLES for record in values]
    every = [record for values in grouped.values() for record in values]
    return {"since": since, "until": until, "calls": len(every), "unfinished_calls": unfinished,
            "settled_unknown_calls": settled_unknown, "unverified_summary_attempts": unverified_summaries,
            "budgeted": token_summary(budgeted), **token_summary(every), "unmetered_sources": unmetered,
            "groups": [{"scene": scene, "role": role, "calls": len(token_records), **token_summary(token_records)}
                       for (scene, role), token_records in sorted(grouped.items())]}
