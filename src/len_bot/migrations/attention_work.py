"""Back up and convert the ADR-0044 database without deleting business facts.

Run with the old runtime stopped. No network, workers, models or delivery paths
are started. Roll back using the matching code/database/media backup as a unit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

import aiosqlite

from len_bot.events.store import EventStore
from len_bot.runtime.attention import HUMAN_INPUTS, RUNTIME_INPUTS
from len_bot.scenes.models import PendingWake, SceneSession


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _fingerprint(connection, table, columns):
    selection = ','.join(_quoted(c) for c in columns) + ' FROM ' + _quoted(table)
    try:
        rows = connection.execute('SELECT rowid,' + selection + ' ORDER BY rowid').fetchall()
    except sqlite3.OperationalError as error:
        if 'no such column: rowid' not in str(error):
            raise
        rows = sorted(connection.execute('SELECT ' + selection).fetchall(), key=repr)
    return hashlib.sha256(repr(rows).encode()).hexdigest(), len(rows)


def _media_manifest(directory):
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(directory.rglob('*')) if path.is_file()}


async def _initialize_extensions(database, boundaries):
    store = EventStore(str(database))
    store._db = await aiosqlite.connect(database)
    try:
        await store.initialize_model_calls()
        await store.initialize_history()
        await store.initialize_jobs()
        for scene_id, boundary in boundaries.items():
            await store._db.execute('INSERT INTO history_origins VALUES(?,?)', (scene_id, boundary))
        await store._db.commit()
    finally:
        await store.close()


def upgrade(database: Path, media_dir: Path, backup_dir: Path):
    database, media_dir, backup_dir = database.resolve(), media_dir.resolve(), backup_dir.resolve()
    if not database.is_file() or not media_dir.is_dir():
        raise ValueError('Existing database and media directory are required')
    if backup_dir.exists() or backup_dir == media_dir or media_dir in backup_dir.parents:
        raise ValueError('Use a new backup directory outside the media directory')
    lsof = shutil.which('lsof')
    if lsof:
        check = subprocess.run([lsof, '-t', str(database), str(database) + '-wal'], capture_output=True, text=True)
        if check.stdout.strip():
            raise RuntimeError('Database is open by another process; stop the runtime and readers before upgrading')
    connection = sqlite3.connect(database, timeout=0)
    try:
        columns = [row[1] for row in connection.execute('PRAGMA table_info(scene_sessions)')]
        if 'last_cognized_event_rowid' not in columns:
            raise ValueError('This command only upgrades the ADR-0044 scene schema; already upgraded or unsupported')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity_check failed before upgrade')
        protected = {}
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            if table in {'scene_sessions', 'reflection_cursors'}:
                continue
            fields = [row[1] for row in connection.execute('PRAGMA table_info(' + _quoted(table) + ')')]
            protected[table] = (fields, _fingerprint(connection, table, fields))
        media_before = _media_manifest(media_dir)
        backup_dir.mkdir(parents=True)
        with sqlite3.connect(backup_dir / 'database.sqlite3') as backup:
            connection.backup(backup)
        shutil.copytree(media_dir, backup_dir / 'media')
        if _media_manifest(backup_dir / 'media') != media_before:
            raise ValueError('Media backup verification failed')
        manifest = {'schema_from':'ADR-0044', 'schema_to':'ADR-0045', 'created_at':time.time(),
                    'database':str(database), 'media_dir':str(media_dir), 'media_hashes':media_before,
                    'protected_tables':{name:{'sha256':data[1][0],'rows':data[1][1]} for name,data in protected.items()}}
        (backup_dir / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        connection.execute('BEGIN EXCLUSIVE')
        boundaries = {}
        try:
            connection.execute('CREATE TABLE IF NOT EXISTS schema_upgrades '
                               '(version TEXT PRIMARY KEY,status TEXT NOT NULL,started_at REAL NOT NULL,completed_at REAL)')
            connection.execute("INSERT INTO schema_upgrades VALUES('ADR-0045','in_progress',?,NULL)", (time.time(),))
            for scene_id, observed, consumed, raw in connection.execute(
                    'SELECT scene_id,last_observed_event_rowid,last_cognized_event_rowid,state_json FROM scene_sessions').fetchall():
                state = json.loads(raw)
                state.pop('last_cognized_event_rowid')
                last = connection.execute('SELECT COALESCE(MAX(rowid),0) FROM events WHERE scene_id=?', (scene_id,)).fetchone()[0]
                state['attention_scanned_event_rowid'] = last
                wakes = []
                for rowid, event_id, kind, actor_id, payload_raw, metadata_raw in connection.execute(
                        'SELECT rowid,id,event_type,actor_id,payload,metadata FROM events WHERE scene_id=? AND rowid>? AND rowid<=? ORDER BY rowid',
                        (scene_id, consumed, observed)):
                    if kind not in HUMAN_INPUTS | RUNTIME_INPUTS:
                        continue
                    payload, metadata = json.loads(payload_raw), json.loads(metadata_raw)
                    if metadata.get('obsolete_task_wake') or metadata.get('obsolete_job_result'):
                        continue
                    if kind == 'REFLECTION_RECORDED' and not metadata.get('needs_review'):
                        continue
                    if kind == 'TASK_DUE' and payload.get('payload', {}).get('kind') == 'agent_job':
                        continue
                    wakes.append(PendingWake(event_id=event_id, rowid=rowid, actor_id=actor_id,
                                             reasons=['migration_unprocessed_input'], certain=True))
                state['pending_wakes'] = [wake.model_dump() for wake in wakes]
                converted = SceneSession.model_validate(state)
                connection.execute('UPDATE scene_sessions SET state_json=? WHERE scene_id=?',
                                   (converted.model_dump_json(), scene_id))
                boundaries[scene_id] = last
            connection.execute('ALTER TABLE scene_sessions RENAME COLUMN last_cognized_event_rowid TO attention_scanned_event_rowid')
            connection.execute("UPDATE scene_sessions SET attention_scanned_event_rowid=json_extract(state_json,'$.attention_scanned_event_rowid')")
            connection.execute('DROP TABLE IF EXISTS reflection_cursors')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        asyncio.run(_initialize_extensions(database, boundaries))
        for table, (fields, fingerprint) in protected.items():
            if _fingerprint(connection, table, fields) != fingerprint:
                raise ValueError('Business facts changed during upgrade: ' + table)
        if _media_manifest(media_dir) != media_before:
            raise ValueError('Original media changed during upgrade')
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Database integrity_check failed after upgrade')
        connection.execute("UPDATE schema_upgrades SET status='completed',completed_at=? WHERE version='ADR-0045'", (time.time(),))
        connection.commit()
        result = {'backup_dir':str(backup_dir), 'scenes':len(boundaries), 'media_files':len(media_before),
                  'facts_preserved':True, 'integrity_check':'ok', 'maintenance_configured':False}
        (backup_dir / 'upgrade-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return result
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--media-dir', type=Path, required=True)
    parser.add_argument('--backup-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(upgrade(args.database, args.media_dir, args.backup_dir), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
