"""Read actual model costs from their existing owners, grouped without a ledger."""
from collections import defaultdict
import json
from .pricing import cost_summary


def usage(store, scenes: list[str] | None, since: float, until: float, *, memory=None, memory_db=None) -> dict:
    grouped = defaultdict(list)
    unfinished = 0
    settled_unknown = 0
    def collect(rows):
        nonlocal unfinished, settled_unknown
        for scene, role, ended, cost in rows:
            grouped[scene, role].append(None if cost is None else json.loads(cost))
            unfinished += ended is None
            settled_unknown += ended is not None and cost is None
    names = '' if scenes is None else ','.join('?' for _ in scenes)
    condition = '1=1' if scenes is None else f'scene IN ({names})'
    params = (since, until) if scenes is None else (*scenes, since, until)
    collect(store.db.execute(
        "SELECT c.scene,CASE WHEN c.plugin IS NULL THEN c.role ELSE 'plugin:' || c.plugin END,c.ended,c.cost "
        "FROM model_calls c "
        f"WHERE {condition if scenes is None else 'c.'+condition} AND c.started>=? AND c.started<?", params))
    for table, role, start, cost in (
        ('audio_calls', 'asr', 'started', 'cost'),
        ('learning_batches', 'learning', 'model_started', 'cost'),
        ('expression_embedding_calls', 'expression_embedding', 'started', 'cost'),
        ('jargon_calls', 'jargon', 'model_started', 'cost'),
        ('sticker_calls', 'sticker', 'model_started', 'cost'),
        ('reply_effect_calls', 'reply_effects', 'model_started', 'cost'),
    ):
        collect(store.db.execute(f"SELECT scene,?,ended,{cost} FROM {table} WHERE {condition} "
                                 f"AND {start}>=? AND {start}<?", (role, *params)))
    collect(store.db.execute(
        "SELECT scene,'worker',json_extract(body,'$.ended'),json_extract(body,'$.response.cost') "
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
        collect(db.execute(f"SELECT scene,'memory_summary',ended,cost FROM memory_summary_runs "
                           f"WHERE {condition} AND model_started>=? AND model_started<?", params))
        collect(db.execute(
            "SELECT j.scene,'memory',json_extract(c.value,'$.ended'),json_extract(c.value,'$.cost') "
            "FROM memory_jobs j,json_each(j.details,'$.calls') c "
            f"WHERE {condition if scenes is None else 'j.'+condition} AND json_extract(c.value,'$.started')>=? "
            "AND json_extract(c.value,'$.started')<?", params))
        collect(db.execute(f"SELECT scene,'memory_embedding',ended,cost FROM memory_embedding_calls "
                           f"WHERE {condition} AND started>=? AND started<?", params))

    all_costs = [c for values in grouped.values() for c in values]
    return {"since": since, "until": until, "calls": len(all_costs), "unfinished_calls": unfinished, "settled_unknown_calls": settled_unknown, "unverified_summary_attempts": unverified_summaries,
            **cost_summary(all_costs), "unmetered_sources": unmetered,
            "groups": [{"scene": scene, "role": role, "calls": len(costs), **cost_summary(costs)}
                       for (scene, role), costs in sorted(grouped.items())]}
