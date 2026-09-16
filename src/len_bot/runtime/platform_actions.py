"""Durable platform attempt facts, separate from QQ delivery receipts."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


async def initialize_platform_actions(store):
    await store._db.execute('''CREATE TABLE IF NOT EXISTS platform_actions (
        action_id TEXT PRIMARY KEY, account_uid INTEGER NOT NULL, resource_id INTEGER NOT NULL,
        action_type TEXT NOT NULL, scene_id TEXT NOT NULL, job_id TEXT NOT NULL,
        job_revision INTEGER NOT NULL, status TEXT NOT NULL, attempted_at REAL,
        updated_at REAL NOT NULL, facts_json TEXT NOT NULL)''')
    await store._db.execute('CREATE INDEX IF NOT EXISTS platform_actions_resource ON platform_actions(account_uid,resource_id,status)')


async def actions_for(store, *, job_id=None, scene_id=None, account_uid=None, resource_id=None):
    clauses, params = [], []
    for key, value in [('job_id', job_id), ('scene_id', scene_id), ('account_uid', account_uid), ('resource_id', resource_id)]:
        if value is not None:
            clauses.append(key + '=?')
            params.append(value)
    rows = await (await store._db.execute('SELECT facts_json,status,attempted_at,updated_at FROM platform_actions'
        + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY updated_at DESC', params)).fetchall()
    return [{**json.loads(row[0]), 'status': row[1], 'attempted_at': row[2], 'updated_at': row[3]} for row in rows]


async def register_action(store, request, requester):
    facts = {**request.parameters, 'action_id': request.action_id, 'action_type': request.action_type,
        'scene_id': request.scene_id, 'job_id': request.job_id, 'job_revision': request.job_revision,
        'requester_qq_uid': requester, 'native_call_id': request.native_call_id, 'reason': 'not_attempted'}
    async with store._write_lock:
        try:
            await store._db.execute('INSERT OR IGNORE INTO platform_actions VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (request.action_id, facts['account_uid'], facts['resource_id'], request.action_type,
                 request.scene_id, request.job_id, request.job_revision, 'not_sent', None, store.clock(), json.dumps(facts)))
            await store._db.commit()
        except BaseException:
            await store._db.rollback()
            raise
    return facts


async def record_outcome(store, action_id, status, reason, **details):
    async with store._write_lock:
        try:
            row = await (await store._db.execute('SELECT facts_json FROM platform_actions WHERE action_id=?', (action_id,))).fetchone()
            if row is None:
                raise ValueError('平台动作未登记')
            facts = {**json.loads(row[0]), 'reason': reason, **details}
            now = store.clock()
            await store._db.execute('UPDATE platform_actions SET status=?,updated_at=?,facts_json=? WHERE action_id=?',
                (status, now, json.dumps(facts, ensure_ascii=False), action_id))
            await store._db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                (f'platform-outcome:{action_id}:{now}', 'PLATFORM_ACTION_RESULT', facts['scene_id'],
                 'plugin:bilibili_content', now, json.dumps({**facts, 'status': status}, ensure_ascii=False),
                 '{"conversation_excluded":true}'))
            await store._db.commit()
        except BaseException:
            await store._db.rollback()
            raise


async def reserve_attempt(connector, call, request, capability, daily_limit):
    store = connector.store
    async with store._write_lock:
        try:
            # Same transaction as the quota and unknown-resource occupancy.
            await connector.job(call, capability)
            rows = await actions_for(store, account_uid=request.parameters['account_uid'], resource_id=request.parameters['resource_id'])
            if any(row['status'] == 'unknown' for row in rows):
                raise ValueError('同账号同资源仍有未知平台动作，不能再次写入')
            own = next(row for row in rows if row['action_id'] == request.action_id)
            if own['attempted_at'] is not None or own['reason'] != 'not_attempted':
                raise ValueError('该平台动作已有结论或尝试，不重放')
            time_config = connector.runtime.config_store.current.time
            if time_config is None:
                raise ValueError('平台动作额度需要明确业务时区')
            day = datetime.fromtimestamp(store.clock(), ZoneInfo(time_config.timezone)).replace(hour=0, minute=0, second=0, microsecond=0)
            used = await (await store._db.execute('''SELECT COUNT(*) FROM platform_actions WHERE account_uid=?
                AND action_type=? AND status IN ('confirmed','unknown') AND attempted_at>=? AND attempted_at<?''',
                (request.parameters['account_uid'], request.action_type, day.timestamp(), (day + timedelta(days=1)).timestamp()))).fetchone()
            if used[0] >= daily_limit:
                raise ValueError('账号该能力的当日写入额度已用完')
            now = store.clock()
            facts = {k: v for k, v in own.items() if k not in {'status', 'attempted_at', 'updated_at'}}
            facts['reason'] = 'attempted_waiting_receipt'
            await store._db.execute('UPDATE platform_actions SET status=?,attempted_at=?,updated_at=?,facts_json=? WHERE action_id=?',
                ('unknown', now, now, json.dumps(facts), request.action_id))
            await store._db.execute('INSERT INTO events VALUES (?,?,?,?,?,?,?)',
                ('platform-attempt:' + request.action_id, 'PLATFORM_ACTION_ATTEMPTED', call.scene_id,
                 'plugin:bilibili_content', now, json.dumps(facts), '{"conversation_excluded":true}'))
            await store._db.commit()
        except BaseException:
            await store._db.rollback()
            raise
