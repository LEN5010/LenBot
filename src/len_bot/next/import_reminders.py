"""Offline transfer of still-future live human reminders, never legacy background work."""

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .config import HostConfig, LabConfig, load_instance_config
from .import_history import _backup, EVENT_COLUMNS
from .store import Store, encode

TASK_COLUMNS = ('id', 'scene_id', 'description', 'due_at', 'status', 'source_event_id', 'payload',
                'created_at', 'wake_event_type', 'wake_match_json', 'origin_episode_id',
                'origin_stimulus_id', 'trigger_event_id', 'origin_mode')
STRICT = ConfigDict(strict=True, extra='forbid')


class LegacyTask(BaseModel):
    model_config = STRICT
    id: str = Field(min_length=1)
    scene_id: str
    description: str
    due_at: float = Field(gt=0, allow_inf_nan=False)
    status: str
    source_event_id: str
    payload: str
    created_at: float = Field(gt=0, allow_inf_nan=False)
    wake_event_type: str | None
    wake_match_json: str | None
    origin_episode_id: str | None
    origin_stimulus_id: str | None
    trigger_event_id: str | None
    origin_mode: str


class LegacyPayload(BaseModel):
    model_config = ConfigDict(strict=True, extra='ignore')
    kind: str | None = None
    requester_qq_uid: str | None = Field(default=None, pattern=r'^[1-9][0-9]*$')
    requester_id: str | None = None
    target_actor_id: str | None = Field(default=None, pattern=r'^user:[1-9][0-9]*$')
    request_source_event_id: str | None = None

    @model_validator(mode='after')
    def reminder_identity(self):
        if self.kind == 'reminder':
            if (self.requester_qq_uid is None or self.requester_id is None
                    or self.target_actor_id is None or not self.request_source_event_id):
                raise ValueError('Human reminder lacks its actual requester, target or original request')
            if self.requester_id != 'user:' + self.requester_qq_uid:
                raise ValueError('Reminder requester_id and requester_qq_uid differ')
        return self


def import_reminders(config: HostConfig | LabConfig) -> dict:
    settings = config.reminder_import
    if settings is None:
        raise ValueError('reminder_import must be explicitly configured in lenbot.config.json')
    source, target, backup = settings.source, config.database, settings.backup
    if not source.is_file():
        raise ValueError(f'Reminder source must be an offline SQLite snapshot: {source}')
    if source == target or source == backup or target == backup:
        raise ValueError('Reminder source, target and backup must be different files')
    for path in (source, target):
        for suffix in ('-wal', '-journal'):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() and sidecar.stat().st_size:
                raise ValueError(f'Reminder import requires a stopped database without nonempty {suffix}: {path}')
    if target.exists() and source.samefile(target):
        raise ValueError('Reminder source and target are the same file')
    if backup.exists():
        raise FileExistsError(f'Reminder target backup already exists: {backup}')
    reports = {scene: {'imported': [], 'already_present': [], 'not_transferred': [], 'reasons': Counter()}
               for scene in settings.scenes}
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as legacy:
        legacy.row_factory = sqlite3.Row
        for table, required in (('tasks', TASK_COLUMNS), ('events', EVENT_COLUMNS)):
            columns = {row[1] for row in legacy.execute(f'PRAGMA table_info({table})')}
            if not set(required) <= columns:
                raise ValueError(f'Legacy {table} lacks required columns {sorted(set(required) - columns)}')
        selected = ','.join('?' for _ in settings.scenes)
        with Store(target) as store:
            _backup(store, backup)
            now = store.now()
            with store.db:
                store.db.execute('BEGIN EXCLUSIVE')
                for row in legacy.execute(f"SELECT {','.join(TASK_COLUMNS)} FROM tasks WHERE scene_id IN ({selected}) ORDER BY rowid",
                                          settings.scenes):
                    original = dict(row)
                    try:
                        task = LegacyTask.model_validate(original)
                    except ValidationError as error:
                        raise ValueError(f'Invalid old reminder row: {error}; raw={repr(original)[:1000]}') from error
                    report = reports[task.scene_id]
                    previous = store.db.execute("SELECT id,status,legacy_source FROM schedules WHERE scene=? "
                                                "AND json_extract(legacy_source,'$.task.id')=?", (task.scene_id, task.id)).fetchone()
                    if previous is not None and json.loads(previous['legacy_source'])['task'] != original:
                        raise ValueError(f'Reminder {task.id} source changed after schedule {previous["id"]} was imported')
                    reason = None
                    if previous is None and task.status != 'pending':
                        reason = f'original status {task.status}'
                    elif previous is None and task.origin_mode != 'live':
                        reason = f'original mode {task.origin_mode}'
                    elif previous is None and (task.wake_event_type is not None or task.wake_match_json is not None or task.trigger_event_id is not None):
                        reason = 'condition-bound or already triggered'
                    elif previous is None and task.due_at <= now:
                        reason = 'original deadline has passed; manual review required'
                    if reason is None:
                        try:
                            payload = LegacyPayload.model_validate_json(task.payload)
                        except ValidationError as error:
                            raise ValueError(f'Invalid old reminder payload {task.id}: {error}; raw={task.payload[:1000]!r}') from error
                        if payload.kind != 'reminder':
                            reason = f'not explicitly a human reminder: {payload.kind}'
                        elif payload.requester_qq_uid == config.bot_qq:
                            reason = 'Bot requester is not a human reminder'
                    if reason is not None:
                        report['not_transferred'].append({'task_id': task.id, 'description': task.description,
                                                         'due_at': task.due_at, 'reason': reason})
                        report['reasons'][reason] += 1
                        continue
                    if not task.description.strip():
                        raise ValueError(f'Reminder {task.id} has no saved reminder text')
                    request = legacy.execute(f"SELECT {','.join(EVENT_COLUMNS)} FROM events WHERE id=? AND scene_id=?",
                                             (payload.request_source_event_id, task.scene_id)).fetchone()
                    if (request is None or request['event_type'] not in {'GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED'}
                            or request['actor_id'] != payload.requester_id or payload.requester_qq_uid == config.bot_qq):
                        raise ValueError(f'Reminder {task.id} original request is missing or does not match its real human requester')
                    origin = {'task': original, 'request': dict(request), 'display_timezone': settings.timezone}
                    if previous is not None:
                        if json.loads(previous['legacy_source']) != origin:
                            raise ValueError(f'Reminder {task.id} conflicts with the source already saved in schedule {previous["id"]}')
                        report['already_present'].append({'task_id': task.id, 'schedule_id': previous['id'], 'status': previous['status']})
                        continue
                    local = config.scene_config(task.scene_id) if isinstance(config, HostConfig) else config
                    pending = store.db.execute("SELECT COUNT(*) FROM schedules WHERE scene=? AND status IN ('pending','blocked')",
                                               (task.scene_id,)).fetchone()[0]
                    if pending >= local.schedules.max_pending:
                        raise ValueError(f'Scene {task.scene_id} reached its configured pending schedule limit')
                    cursor = store.db.execute("INSERT INTO schedules(scene,created,due_at,timezone,note,target,requester,status,legacy_source) "
                                              "VALUES(?,?,?,?,?,?,?,'pending',?)",
                                              (task.scene_id, task.created_at, task.due_at, settings.timezone, task.description,
                                               payload.target_actor_id.removeprefix('user:'), payload.requester_qq_uid, encode(origin)))
                    report['imported'].append({'task_id': task.id, 'schedule_id': cursor.lastrowid,
                                               'requester': payload.requester_qq_uid,
                                               'target': payload.target_actor_id.removeprefix('user:'), 'due_at': task.due_at,
                                               'scheduled_execution_enabled': local.schedules.enabled})
    return {'source': str(source), 'target': str(target), 'backup': str(backup), 'scenes': reports,
            'notice': 'Only future live human one-shot reminders transferred. Source remains read-only; '
                      'the operator must stop old triggering before starting the destination. '
                      'Claimed, condition-bound, overdue and other work remains for manual handoff.'}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Import takes no overrides; configure reminder_import in the root lenbot.config.json')
    print(encode(import_reminders(load_instance_config(Path.cwd()))))


if __name__ == '__main__':
    main()
