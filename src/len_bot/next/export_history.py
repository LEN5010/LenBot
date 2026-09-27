"""Offline append of new-period messages to an existing legacy database."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time

from len_bot.events.models import Event
from len_bot.scenes.models import SceneSession
from len_bot.scenes.reducer import SceneReducer

from .config import HostConfig, LabConfig, load_instance_config
from .messages import ChatMessage, Segment, Sender
from .rollback_messages import convert_next_message
from .store import encode


EVENT_COLUMNS = ("id", "event_type", "scene_id", "actor_id", "timestamp", "payload", "metadata")
MESSAGE_TYPES = ("GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED", "MESSAGE_SENT", "MESSAGE_SEND_FAILED")


def _offline_file(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Offline SQLite file does not exist: {path}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"Offline database has nonempty {suffix}: {path}")


def _backup(db: sqlite3.Connection, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(path)) as backup:
            db.backup(backup)
    except BaseException:
        path.unlink()
        raise


def _event(row: sqlite3.Row) -> dict:
    value = {name: row[name] for name in EVENT_COLUMNS}
    try:
        value["payload"] = json.loads(value["payload"])
        value["metadata"] = json.loads(value["metadata"])
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid legacy event {value['id']}: {error}; raw={repr(value)[:500]}") from error
    return value


def _sessions(db: sqlite3.Connection, scenes: list[str], bot_qq: str) -> dict[str, SceneSession]:
    sessions = {}
    for scene in scenes:
        if db.execute("SELECT 1 FROM pending_runtime_events WHERE scene_id=? LIMIT 1", (scene,)).fetchone():
            raise ValueError(f"Legacy scene {scene} has pending_runtime_events; settle them before export")
        saved = db.execute("SELECT * FROM scene_sessions WHERE scene_id=?", (scene,)).fetchone()
        through = db.execute("SELECT COALESCE(MAX(rowid),0) FROM events WHERE scene_id=?", (scene,)).fetchone()[0]
        if saved is None:
            session = SceneSession(scene_id=scene)
            # An archive without a session still has actual participant/send
            # facts. Rebuild only that pure projection, never old attention.
            for row in db.execute("SELECT * FROM events WHERE scene_id=? ORDER BY rowid", (scene,)):
                session = SceneReducer.reduce(session, Event(**_event(row)), f"user:{bot_qq}")
            session.last_observed_event_rowid = session.attention_scanned_event_rowid = through
        else:
            try:
                session = SceneSession.model_validate_json(saved["state_json"])
            except ValueError as error:
                raise ValueError(f"Invalid legacy session {scene}: {error}; raw={saved['state_json'][:500]}") from error
            if session.pending_wakes:
                raise ValueError(f"Legacy scene {scene} has pending_wakes; settle them before export")
            if (session.scene_id != scene or saved["version"] != session.version
                    or saved["last_observed_event_rowid"] != session.last_observed_event_rowid
                    or saved["attention_scanned_event_rowid"] != session.attention_scanned_event_rowid):
                raise ValueError(f"Legacy scene {scene} has inconsistent saved session columns and state_json")
            if session.last_observed_event_rowid != through or session.attention_scanned_event_rowid != through:
                raise ValueError(f"Legacy scene {scene} has unread events; settle them before export")
        sessions[scene] = session
    return sessions


def _message(row: sqlite3.Row) -> tuple[ChatMessage, dict]:
    body = json.loads(row["body"])
    raw = None if row["raw"] is None else json.loads(row["raw"])
    message = ChatMessage(**{**body, "sender": Sender(**body["sender"]),
                             "segments": [Segment(**part) for part in body["segments"]]})
    if message.scene != row["scene"] or message.platform_message_id != row["platform_id"]:
        raise ValueError("New message body does not match its scene/platform identity columns")
    return message, {"seq": row["seq"], "body": body, "raw": raw, "received_at": row["received_at"]}


def _originals_present(db: sqlite3.Connection, packet: dict) -> bool:
    raw = packet["raw"]
    if raw is None or "legacy_events" not in raw:
        return False
    for original in raw["legacy_events"]:
        event = original["event"]
        row = db.execute("SELECT * FROM events WHERE id=?", (event["id"],)).fetchone()
        if row is None or _event(row) != event:
            raise ValueError(f"Legacy original {event['id']} is missing or differs in target")
    return True


def _append_event(db: sqlite3.Connection, event: Event) -> int | None:
    record = event.model_dump(mode="json")
    previous = db.execute("SELECT * FROM events WHERE id=?", (event.id,)).fetchone()
    if previous is not None:
        if _event(previous) != record:
            raise ValueError(f"Conflicting existing event id {event.id}")
        return None
    platform_id = event.payload["message_id"]
    if platform_id is not None:
        previous = db.execute(
            "SELECT id FROM events WHERE scene_id=? AND CAST(json_extract(payload,'$.message_id') AS TEXT)=? "
            f"AND event_type IN ({','.join('?' for _ in MESSAGE_TYPES)}) LIMIT 1",
            (event.scene_id, str(platform_id), *MESSAGE_TYPES),
        ).fetchone()
        if previous is not None:
            raise ValueError(f"Conflicting platform message {platform_id} in {event.scene_id}: {previous['id']} and {event.id}")
    cursor = db.execute(
        f"INSERT INTO events ({','.join(EVENT_COLUMNS)}) VALUES (?,?,?,?,?,?,?)",
        (event.id, event.event_type.value, event.scene_id, event.actor_id, event.timestamp,
         encode(event.payload), encode(event.metadata)),
    )
    if event.raw_text:
        db.execute("INSERT INTO events_fts(event_id,scene_id,actor_id,content) VALUES (?,?,?,?)",
                   (event.id, event.scene_id, event.actor_id, event.raw_text))
    return cursor.lastrowid


def export_history(config: LabConfig | HostConfig) -> dict:
    settings = config.history_export
    if settings is None:
        raise ValueError("history_export must be explicitly configured in lenbot.config.json")
    source, target, backup = config.database, settings.target, settings.backup
    _offline_file(source)
    _offline_file(target)
    if source.samefile(target):
        raise ValueError("History source and target refer to the same file")
    if backup.exists():
        raise FileExistsError(f"History backup already exists: {backup}")
    reports = {scene: {"source_messages": 0, "messages": 0, "legacy_messages": 0,
                      "already_exported": 0, "send_states": Counter()}
               for scene in settings.scenes}
    placeholders = ",".join("?" for _ in settings.scenes)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as new:
        new.row_factory = sqlite3.Row
        if (new.execute("PRAGMA application_id").fetchone()[0] != 0x4C424E31
                or new.execute("PRAGMA user_version").fetchone()[0] != 13):
            raise ValueError(f"History export requires current next-core database format 13: {source}")
        with closing(sqlite3.connect(target.as_uri() + "?mode=rw", uri=True)) as old:
            old.row_factory = sqlite3.Row
            tables = {row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"events", "events_fts", "scene_sessions", "pending_runtime_events", "tasks"}
            if not required <= tables:
                raise ValueError(f"Legacy target lacks tables {sorted(required - tables)}")
            _sessions(old, settings.scenes, config.bot_qq)
            _backup(old, backup)
            with old:
                old.execute("BEGIN EXCLUSIVE")
                sessions = _sessions(old, settings.scenes, config.bot_qq)
                for row in new.execute(f"SELECT * FROM messages WHERE scene IN ({placeholders}) ORDER BY seq", settings.scenes):
                    try:
                        message, packet = _message(row)
                        report = reports[message.scene]
                        report["source_messages"] += 1
                        if _originals_present(old, packet):
                            report["legacy_messages"] += 1
                            continue
                        reply = None if message.reply_to is None else new.execute(
                            "SELECT json_extract(body,'$.is_self') FROM messages WHERE scene=? AND platform_id=?",
                            (message.scene, message.reply_to),
                        ).fetchone()
                        event = convert_next_message(message, packet["raw"], bot_qq=config.bot_qq,
                                                     reply_is_self=reply is not None and reply[0] == 1)
                        event.metadata["next_message"] = packet
                        rowid = _append_event(old, event)
                        if rowid is None:
                            report["already_exported"] += 1
                            continue
                        report["messages"] += 1
                        report["send_states"][message.send_status] += 1
                        session = SceneReducer.reduce(sessions[message.scene], event, f"user:{config.bot_qq}")
                        session.last_observed_event_rowid = session.attention_scanned_event_rowid = rowid
                        sessions[message.scene] = session
                    except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
                        raise ValueError(
                            f"Next message at seq {row['seq']}: {error}; body={row['body'][:500]}; "
                            f"raw={repr(row['raw'])[:300]}"
                        ) from error
                for scene, report in reports.items():
                    if not report["source_messages"]:
                        raise ValueError(f"New source has no messages for {scene}")
                    if report["messages"]:
                        session = sessions[scene]
                        old.execute(
                            "INSERT INTO scene_sessions VALUES (?,?,?,?,?,?) ON CONFLICT(scene_id) DO UPDATE SET "
                            "version=excluded.version,last_observed_event_rowid=excluded.last_observed_event_rowid,"
                            "attention_scanned_event_rowid=excluded.attention_scanned_event_rowid,"
                            "state_json=excluded.state_json,updated_at=excluded.updated_at",
                            (scene, session.version, session.last_observed_event_rowid,
                             session.attention_scanned_event_rowid, session.model_dump_json(), time.time()),
                        )
                    report["send_states"] = dict(report["send_states"])
                    report["retained_new_data"] = {
                        "schedules": dict(new.execute("SELECT status,COUNT(*) FROM schedules WHERE scene=? GROUP BY status", (scene,))),
                        **{table: new.execute(f"SELECT COUNT(*) FROM {table} WHERE scene=?", (scene,)).fetchone()[0]
                           for table in ("web_documents", "image_cache")},
                    }
                    report["legacy_open_tasks"] = dict(old.execute(
                        "SELECT status,COUNT(*) FROM tasks WHERE scene_id=? "
                        "AND status IN ('pending','claimed','processing','review_required') GROUP BY status", (scene,),
                    ))
    return {"source": str(source), "target": str(target), "backup": str(backup), "scenes": reports,
            "not_restored": ["mind history and model calls", "schedules", "media assets and image cache", "web documents", "background ownership"]}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("History export takes no arguments; use the instance's lenbot.config.json")
    print(encode(export_history(load_instance_config(Path.cwd()))))


if __name__ == "__main__":
    main()
