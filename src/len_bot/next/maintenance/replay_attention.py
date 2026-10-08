"""Offline attention replay using retained messages, wakes and actual expressions; no model calls."""

import argparse
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from ..chat.attention import is_direct
from ..chat.usage import wake_usage
from ..config import load_host_config
from ..platform.messages import plain_text
from ..storage.codec import decode_message
from ..storage.sqlite import connect
from .wake_history import request_wake


def replay(db, since: float, until: float, *, scene: str | None, timezone: str, aliases: list[str]) -> dict:
    """Suppress focus after clean silence until the next recorded expression or direct contact.

    Recorded speech stays on the timeline even when listed as potentially lost. Ambient
    admission and model decisions cannot be predicted offline; every suppressed batch is
    still available to ambient and to the next turn at runtime.
    """
    db.row_factory = sqlite3.Row
    condition = '' if scene is None else 'AND t.scene=?'
    rows = db.execute(
        'SELECT t.*,c.request FROM turns t LEFT JOIN model_calls c ON c.id='
        "(SELECT MIN(id) FROM model_calls WHERE turn_id=t.id AND role='mind') "
        f'WHERE t.started<? {condition} ORDER BY t.started,t.id', (until, *((scene,) if scene else ())))
    turns = []
    for row in rows:
        turn = dict(row)
        request = turn.pop('request')
        channel, batch = ('unknown', '') if request is None else request_wake(json.loads(request))
        turn['wake_channel'] = turn.get('wake_channel', channel)
        turn['batch'] = batch
        turns.append(turn)
    contacts = defaultdict(list)
    alias_messages = []
    words = [word.strip().casefold() for word in aliases if word.strip()]
    own_ids = { (row[0], row[1]) for row in db.execute(
        "SELECT scene,platform_id FROM messages WHERE json_extract(body,'$.is_self')=1") }
    for row in db.execute('SELECT seq,scene,body,COALESCE(received_at,json_extract(body,\'$.time\')) AS at '
                          'FROM messages WHERE COALESCE(received_at,json_extract(body,\'$.time\'))<? ORDER BY at,seq', (until,)):
        if scene is not None and row['scene'] != scene:
            continue
        message = decode_message(row['body'])
        if (message.is_self and message.send_status in {'sent', 'received', 'simulated'}
                or is_direct(message) or message.reply_to is not None and (message.scene, message.reply_to) in own_ids):
            contacts[message.scene].append(row['at'])
        matched = [word for word in words if word in plain_text(message).casefold()]
        if since <= row['at'] < until and not message.is_self and matched:
            alias_messages.append({'seq': row['seq'], 'scene': message.scene, 'at': row['at'],
                                   'aliases': matched, 'message': json.loads(row['body'])})
    closed = {}
    reduced, lost = [], []
    for turn in turns:
        key, start = turn['scene'], turn['started']
        ended = turn['ended']
        channel = turn['wake_channel']
        if key in closed and any(closed[key] < at <= start for at in contacts[key]):
            del closed[key]
        if channel == 'direct':
            closed.pop(key, None)
        suppressed = channel == 'focus' and key in closed
        if suppressed and start >= since:
            item = {k: turn[k] for k in ('id', 'scene', 'started', 'ended', 'status', 'batch', 'first_expression_at')}
            item['closed_at'] = closed[key]
            item['expressions'] = []
            for call in db.execute("SELECT response,mind_entry_seq FROM model_calls WHERE turn_id=? AND role='mind' ORDER BY id", (turn['id'],)):
                if call['response'] is None:
                    continue
                response = json.loads(call['response'])
                for tool in response['message'].get('tool_calls') or []:
                    if tool['function']['name'] in {'say', 'react', 'message_reaction'}:
                        result = db.execute("SELECT message FROM mind_entries WHERE scene=? AND seq>? "
                                            "AND json_extract(message,'$.tool_call_id')=? ORDER BY seq LIMIT 1",
                                            (key, call['mind_entry_seq'], tool['id'])).fetchone()
                        item['expressions'].append({'tool': tool['function'],
                                                    'result': None if result is None else json.loads(result[0])['content']})
            reduced.append(item)
            if turn['first_expression_at'] is not None:
                lost.append(item)
        if turn['first_expression_at'] is not None:
            closed.pop(key, None)
        elif channel == 'focus' and turn['status'] == 'settled' and ended is not None:
            failed = db.execute(
                "SELECT 1 FROM mind_entries WHERE scene=? AND created>=? AND created<=? "
                "AND json_extract(message,'$.role')='tool' AND json_extract(message,'$.content') LIKE '%失败：%' LIMIT 1",
                (key, start, ended)).fetchone()
            contact_during = any(start < at <= ended for at in contacts[key])
            if failed is None and not contact_during:
                closed.setdefault(key, ended)
    # The reporting query uses the same grouping and token scope as the panel.
    with closing(sqlite3.connect(':memory:')) as reporting:
        reporting.execute('CREATE TABLE turns(id TEXT,scene TEXT,started REAL,wake_channel TEXT,first_expression_at REAL)')
        reporting.execute('CREATE TABLE model_calls(turn_id TEXT,role TEXT,tokens TEXT)')
        for turn in turns:
            if since <= turn['started'] < until:
                reporting.execute('INSERT INTO turns VALUES (?,?,?,?,?)',
                                  tuple(turn[k] for k in ('id', 'scene', 'started', 'wake_channel', 'first_expression_at')))
                reporting.executemany('INSERT INTO model_calls VALUES (?,?,?)', db.execute(
                    'SELECT turn_id,role,tokens FROM model_calls WHERE turn_id=?', (turn['id'],)))
        baseline = wake_usage(SimpleNamespace(db=reporting), None, since, until, timezone)
    return {'baseline': baseline, 'reduced_wakes': reduced, 'lost_speech': lost, 'alias_messages': alias_messages}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--date', type=date.fromisoformat, required=True)
    parser.add_argument('--scene')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--aliases', nargs='*', default=[])
    args = parser.parse_args()
    config = load_host_config(args.root.resolve())
    zone = config.timezone if args.scene is None else config.scene_timezone(args.scene)
    start = datetime.combine(args.date, datetime.min.time(), ZoneInfo(zone))
    with closing(connect(config.database, readonly=True)) as db:
        result = replay(db, start.timestamp(), (start + timedelta(days=1)).timestamp(),
                        scene=args.scene, timezone=zone, aliases=args.aliases)
    args.output.mkdir(parents=True, exist_ok=True)
    for name, records in result.items():
        (args.output / f'{name}.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    summary = {'date': args.date.isoformat(), 'timezone': zone, 'scene': args.scene,
               'reduced_wakes': len(result['reduced_wakes']), 'avoided_silence': len(result['reduced_wakes']) - len(result['lost_speech']),
               'lost_speech': len(result['lost_speech']), 'alias_messages': len(result['alias_messages']),
               'unknown_wakes': sum(row['wakes'] for row in result['baseline'] if row['channel'] == 'unknown')}
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
