"""Opening wakes, successful expressions and mind tokens by scene-local day."""

from collections import defaultdict
from datetime import datetime
import json
from zoneinfo import ZoneInfo

from ..models.tokens import token_summary


def wake_usage(store, scenes: list[str] | None, since: float, until: float, timezone: str) -> list[dict]:
    condition = '' if scenes is None else f"AND scene IN ({','.join('?' for _ in scenes)})"
    turns = list(store.db.execute(
        f'SELECT id,scene,started,wake_channel,first_expression_at FROM turns WHERE started>=? AND started<? {condition}',
        (since, until, *(scenes or []))))
    tokens = defaultdict(list)
    for turn_id, body in store.db.execute(
        'SELECT c.turn_id,c.tokens FROM model_calls c JOIN turns t ON t.id=c.turn_id '
        "WHERE t.started>=? AND t.started<? AND c.role='mind'", (since, until)):
        tokens[turn_id].append(None if body is None else json.loads(body))
    grouped = {}
    zone = ZoneInfo(timezone)
    for turn_id, scene, started, channel, expressed_at in turns:
        day = datetime.fromtimestamp(started, zone).date().isoformat()
        row = grouped.setdefault((scene, day, channel),
                                 {'scene': scene, 'day': day, 'channel': channel, 'wakes': 0, 'spoken': 0, 'tokens': []})
        row['wakes'] += 1
        row['spoken'] += expressed_at is not None
        row['tokens'].extend(tokens[turn_id])
    result = []
    for _, row in sorted(grouped.items()):
        counts = token_summary(row.pop('tokens'))
        result.append({**row, 'speech_rate': row['spoken'] / row['wakes'], **counts})
    return result
