"""Offline reconciliation of imported reminders without re-triggering consumed wakes."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import EpochSeconds, HostConfig, LabConfig, load_instance_config
from .instance_lock import instance_lock
from .export_history import _backup, _sessions
from .import_history import EVENT_COLUMNS, _reject_constant
from .import_media import stopped
from .import_reminders import LegacyPayload, LegacyTask, TASK_COLUMNS
from .store import FORMAT_VERSION, encode


STRICT = ConfigDict(strict=True, extra='forbid', allow_inf_nan=False)


class OriginalRequest(BaseModel):
    model_config = STRICT
    id: str
    event_type: Literal['GROUP_MESSAGE_RECEIVED', 'PRIVATE_MESSAGE_RECEIVED']
    scene_id: str
    actor_id: str
    timestamp: EpochSeconds
    payload: str
    metadata: str


class OriginalReminder(BaseModel):
    model_config = STRICT
    task: LegacyTask
    request: OriginalRequest
    display_timezone: str


class SavedSchedule(BaseModel):
    model_config = STRICT
    id: int = Field(gt=0)
    scene: str
    created: EpochSeconds
    due_at: EpochSeconds
    timezone: str
    note: str
    target: str = Field(pattern=r'^(self|[1-9][0-9]*)$')
    requester: str | None = Field(pattern=r'^[1-9][0-9]*$')
    status: Literal['pending', 'blocked', 'delivered', 'cancelled']
    delivered_at: EpochSeconds | None
    reason: str | None
    interval_seconds: int | None
    cron: str | None
    legacy_source: str | None


class NextReminderRecord(BaseModel):
    model_config = STRICT
    schedule: SavedSchedule
    pending: Literal['restore', 'hold']
    legacy_status: Literal['pending', 'cancelled']
    held_reason: str | None


def original_reminder(schedule: SavedSchedule, bot_qq: str) -> OriginalReminder:
    origin = OriginalReminder.model_validate_json(schedule.legacy_source)
    task, request = origin.task, origin.request
    payload = LegacyPayload.model_validate_json(task.payload)
    if (task.status != 'pending' or task.origin_mode != 'live' or payload.kind != 'reminder'
            or task.wake_event_type is not None or task.wake_match_json is not None or task.trigger_event_id is not None):
        raise ValueError('Archived source is not an unclaimed live one-shot human reminder')
    if (request.id != payload.request_source_event_id or request.actor_id != payload.requester_id
            or request.scene_id != task.scene_id or payload.requester_qq_uid == bot_qq):
        raise ValueError('Archived original reminder request and real human identity differ')
    if (schedule.scene != task.scene_id or schedule.created != task.created_at
            or schedule.due_at != task.due_at or schedule.note != task.description
            or schedule.requester != payload.requester_qq_uid
            or schedule.target != payload.target_actor_id.removeprefix('user:')
            or schedule.timezone != origin.display_timezone
            or schedule.interval_seconds is not None or schedule.cron is not None):
        raise ValueError('Imported reminder identity/content/timing differs from its actual source')
    if (schedule.status == 'delivered') != (schedule.delivered_at is not None):
        raise ValueError('Imported one-shot reminder state and actual conversation delivery time differ')
    return origin


def desired_record(schedule: SavedSchedule, *, pending: Literal['restore', 'hold'], now: float) -> NextReminderRecord:
    if schedule.status == 'delivered':
        reason = 'Already delivered to the new conversation; platform reminder delivery is not asserted'
    elif schedule.status == 'cancelled':
        reason = 'Cancelled in the new core'
    elif schedule.status == 'blocked':
        reason = 'Blocked in the new core; no automatic old-core retry'
    elif pending == 'hold':
        reason = 'Operator explicitly selected hold for old triggering'
    elif schedule.due_at <= now:
        reason = 'Original pending deadline has passed; explicit review required, no immediate old trigger'
    else:
        reason = None
    return NextReminderRecord(schedule=schedule, pending=pending,
                              legacy_status='pending' if reason is None else 'cancelled', held_reason=reason)


def project_task(origin: OriginalReminder, record: NextReminderRecord) -> dict:
    task = origin.task.model_dump()
    payload = json.loads(task['payload'], parse_constant=_reject_constant)
    if 'next_schedule' in payload:
        raise ValueError('Original reminder payload already contains next_schedule; no overwrite or format guessing')
    payload['next_schedule'] = record.model_dump(mode='json')
    task['status'], task['payload'] = record.legacy_status, encode(payload)
    return task


def export_reminders(config: HostConfig | LabConfig) -> dict:
    settings = config.reminder_export
    if settings is None:
        raise ValueError('Configure reminder_export explicitly in the root lenbot.config.json')
    source, target, backup = config.database, settings.target, settings.backup
    stopped(source)
    stopped(target)
    if source.samefile(target):
        raise ValueError('Reminder source and target must be different files')
    if backup.exists():
        raise FileExistsError(f'Reminder target backup already exists: {backup}')
    now = time.time()
    reports = {scene: {'restored_pending': [], 'trigger_disabled': [], 'already_present': [],
                      'not_transferred': []} for scene in settings.scenes}
    placeholders = ','.join('?' for _ in settings.scenes)
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro&immutable=1', uri=True)) as new:
        new.row_factory = sqlite3.Row
        if (new.execute('PRAGMA application_id').fetchone()[0] != 0x4C424E31
                or new.execute('PRAGMA user_version').fetchone()[0] != FORMAT_VERSION):
            raise ValueError(f'Reminder export requires current new database format {FORMAT_VERSION}: {source}')
        active = new.execute(f'SELECT id,scene,status FROM turns WHERE scene IN ({placeholders}) AND ended IS NULL',
                             settings.scenes).fetchall()
        if active:
            raise ValueError(f'Settle actual unfinished new conversations before rollback: {[dict(row) for row in active]!r}')
        with closing(sqlite3.connect(target.as_uri() + '?mode=rw', uri=True)) as old:
            old.row_factory = sqlite3.Row
            columns = {row[1] for row in old.execute('PRAGMA table_info(tasks)')}
            if columns != set(TASK_COLUMNS):
                raise ValueError(f'Legacy tasks differs from the offline reminder contract: columns={sorted(columns)!r}')
            _sessions(old, settings.scenes, config.bot_qq)
            _backup(old, backup)
            with old:
                old.execute('BEGIN EXCLUSIVE')
                _sessions(old, settings.scenes, config.bot_qq)
                for row in new.execute(f'SELECT * FROM schedules WHERE scene IN ({placeholders}) ORDER BY id', settings.scenes):
                    raw = dict(row)
                    try:
                        schedule = SavedSchedule.model_validate(raw)
                        report = reports[schedule.scene]
                        if schedule.legacy_source is None:
                            report['not_transferred'].append({
                                'schedule': raw,
                                'reason': 'No original legacy human request/episode; retained in new database without inferred identity',
                            })
                            continue
                        origin = original_reminder(schedule, config.bot_qq)
                        request = old.execute(f"SELECT {','.join(EVENT_COLUMNS)} FROM events WHERE id=?",
                                              (origin.request.id,)).fetchone()
                        if request is None or dict(request) != origin.request.model_dump():
                            raise ValueError(f'Original human request is missing or differs in target: {origin.request.id!r}')
                        previous = old.execute(f"SELECT {','.join(TASK_COLUMNS)} FROM tasks WHERE id=?",
                                               (origin.task.id,)).fetchone()
                        if previous is None:
                            raise ValueError(f'Original legacy reminder is missing: {origin.task.id!r}')
                        existing = dict(previous)
                        prior = None
                        if existing != origin.task.model_dump():
                            payload = json.loads(existing['payload'], parse_constant=_reject_constant)
                            if 'next_schedule' not in payload:
                                raise ValueError(f'Legacy reminder was changed/claimed outside this transfer: {origin.task.id!r}')
                            prior = NextReminderRecord.model_validate(payload['next_schedule'])
                            if (prior.schedule.id != schedule.id
                                    or original_reminder(prior.schedule, config.bot_qq) != origin
                                    or project_task(origin, prior) != existing):
                                raise ValueError(f'Legacy reminder differs from its prior saved projection: {origin.task.id!r}')
                        record = desired_record(schedule, pending=settings.pending, now=now)
                        item = {'task_id': origin.task.id, 'schedule_id': schedule.id,
                                'source_status': schedule.status, 'legacy_status': record.legacy_status,
                                'conversation_delivered_at': schedule.delivered_at,
                                'held_reason': record.held_reason}
                        if prior == record:
                            report['already_present'].append(item)
                            continue
                        desired = project_task(origin, record)
                        old.execute('UPDATE tasks SET status=?,payload=? WHERE id=? AND scene_id=?',
                                    (desired['status'], desired['payload'], origin.task.id, schedule.scene))
                        category = 'restored_pending' if record.legacy_status == 'pending' else 'trigger_disabled'
                        report[category].append(item)
                    except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
                        raise ValueError(f'Reminder rollback at schedule {raw["id"]}: {error}; raw={repr(raw)[:1500]}') from error
    return {'source': str(source), 'target': str(target), 'backup': str(backup), 'observed_at': now,
            'pending': settings.pending, 'scenes': reports,
            'notice': 'Only actual imported one-shot reminders reconciled. cancelled means old triggering disabled; '
                      'delivered means conversation input, not a platform receipt. No new wake, send, route switch or startup. '
                      'Native new schedules, periodic/autonomous work and background ownership remain in the new database.'}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit('Reminder export takes no overrides; stop the instance and configure its root file')
    with instance_lock(Path.cwd()):
        print(encode(export_reminders(load_instance_config(Path.cwd()))))


if __name__ == '__main__':
    main()
