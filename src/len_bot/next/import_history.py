"""Explicit offline import of actual legacy messages, never old runtime state."""

from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from string import Template
import sys
from zoneinfo import ZoneInfo

from .config import HostConfig, LabConfig, load_instance_config
from .instance_lock import instance_lock
from .legacy_messages import convert_legacy_message
from .messages import ChatMessage, render_message
from .store import Store, encode


MESSAGE_TYPES = ("GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED", "MESSAGE_SENT", "MESSAGE_SEND_FAILED")
EVENT_COLUMNS = ("id", "event_type", "scene_id", "actor_id", "timestamp", "payload", "metadata")
SCENE_TABLES = ("messages", "mind_entries", "mind_sessions", "turns", "schedules", "web_documents", "image_cache")
PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "next_history_import.md"


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _event(row: sqlite3.Row) -> dict:
    event = {name: row[name] for name in EVENT_COLUMNS}
    try:
        event["payload"] = json.loads(event["payload"], parse_constant=_reject_constant)
        event["metadata"] = json.loads(event["metadata"], parse_constant=_reject_constant)
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid legacy row {row['rowid']}: {error}; raw={repr(event)[:500]}") from error
    return event


def _empty_scenes(store: Store, scenes: list[str]) -> None:
    for scene in scenes:
        for table in SCENE_TABLES:
            if store.db.execute(f"SELECT 1 FROM {table} WHERE scene=? LIMIT 1", (scene,)).fetchone():
                raise ValueError(f"History import requires an empty target scene: {scene} already has {table}")


def _backup(store: Store, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(path)) as backup:
            store.db.backup(backup)
    except BaseException as error:
        try:
            path.unlink()
        except OSError as cleanup_error:
            error.add_note(f'Incomplete history backup cleanup also failed at {path}: {cleanup_error}')
        raise


def _store_message(store: Store, message: ChatMessage, event: dict, rowid: int) -> bool:
    source = {"rowid": rowid, "event": event}
    previous = None if message.platform_message_id is None else store.db.execute(
        "SELECT seq,raw FROM messages WHERE scene=? AND platform_id=?",
        (message.scene, message.platform_message_id),
    ).fetchone()
    if previous is None:
        store._save_message(message, {"legacy_events": [source]}, received_at=None)
        return True
    raw = json.loads(previous["raw"])
    first = raw["legacy_events"][0]["event"]
    # This exact duplicate shape exists in the offline source. Every original
    # event remains present; the platform identity still denotes one message.
    if ({key: value for key, value in first.items() if key != "id"}
            != {key: value for key, value in event.items() if key != "id"}):
        raise ValueError(
            f"Conflicting platform message {message.platform_message_id} in {message.scene}: "
            f"legacy events {first['id']} and {event['id']}"
        )
    raw["legacy_events"].append(source)
    store.db.execute("UPDATE messages SET raw=? WHERE seq=?", (encode(raw), previous["seq"]))
    return False


def import_history(config: LabConfig | HostConfig) -> dict:
    settings = config.history_import
    if settings is None:
        raise ValueError("history_import must be explicitly configured in lenbot.config.json")
    source, target, backup = settings.source, config.database, settings.backup
    if not source.is_file():
        raise ValueError(f"History source is not an offline SQLite file: {source}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"History source has a nonempty {suffix} file; use a complete offline snapshot: {source}")
    if target.exists() and source.samefile(target):
        raise ValueError("History source and target refer to the same file")
    if backup.exists():
        raise FileExistsError(f"History backup already exists: {backup}")
    placeholders = ",".join("?" for _ in settings.scenes)
    reports = {scene: {"source_messages": 0, "messages": 0, "duplicate_events": 0,
                      "text_only_records": 0, "unknown_nicknames": 0, "legacy_media_segments": 0,
                      "send_states": Counter(), "unconverted_events": {}, "unresolved_delivery_attempts": 0}
               for scene in settings.scenes}
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as legacy:
        legacy.row_factory = sqlite3.Row
        columns = {row[1] for row in legacy.execute("PRAGMA table_info(events)")}
        if not set(EVENT_COLUMNS) <= columns:
            raise ValueError(f"Legacy events schema lacks {sorted(set(EVENT_COLUMNS) - columns)}")
        for scene, kind, count in legacy.execute(
            f"SELECT scene_id,event_type,COUNT(*) FROM events WHERE scene_id IN ({placeholders}) "
            "GROUP BY scene_id,event_type", settings.scenes,
        ):
            if kind not in MESSAGE_TYPES:
                reports[scene]["unconverted_events"][kind] = count
        for scene in settings.scenes:
            reports[scene]["unresolved_delivery_attempts"] = legacy.execute(
                "SELECT COUNT(*) FROM events a WHERE a.scene_id=? AND a.event_type='DELIVERY_ATTEMPTED' "
                "AND NOT EXISTS (SELECT 1 FROM events b WHERE b.scene_id=a.scene_id "
                "AND b.event_type IN ('MESSAGE_SENT','MESSAGE_SEND_FAILED','FILE_UPLOADED','FILE_UPLOAD_FAILED','ACTION_SHADOWED') "
                "AND json_extract(b.payload,'$.action_id')=json_extract(a.payload,'$.action_id'))",
                (scene,),
            ).fetchone()[0]
        with Store(target) as store:
            _empty_scenes(store, settings.scenes)
            _backup(store, backup)
            with store.db:
                store.db.execute("BEGIN EXCLUSIVE")
                _empty_scenes(store, settings.scenes)
                rows = legacy.execute(
                    f"SELECT rowid,{','.join(EVENT_COLUMNS)} FROM events WHERE scene_id IN ({placeholders}) "
                    f"AND event_type IN ({','.join('?' for _ in MESSAGE_TYPES)}) ORDER BY rowid",
                    (*settings.scenes, *MESSAGE_TYPES),
                )
                for row in rows:
                    event = _event(row)
                    try:
                        message = convert_legacy_message(event, bot_qq=config.bot_qq)
                        inserted = _store_message(store, message, event, row["rowid"])
                    except (ValueError, TypeError, KeyError, sqlite3.Error) as error:
                        raise ValueError(
                            f"Legacy event {event['id']} at row {row['rowid']}: {error}; raw={repr(event)[:500]}"
                        ) from error
                    report = reports[message.scene]
                    report["source_messages"] += 1
                    report["duplicate_events"] += not inserted
                    if not inserted:
                        continue
                    report["messages"] += 1
                    report["text_only_records"] += event["payload"].get("segments") is None
                    report["unknown_nicknames"] += message.sender.nickname is None
                    report["legacy_media_segments"] += sum("legacy_asset_id" in item.data for item in message.segments)
                    report["send_states"][message.send_status] += 1
                template = Template(PROMPT.read_text(encoding="utf-8"))
                for scene, report in reports.items():
                    if not report["messages"]:
                        raise ValueError(f"Legacy source has no convertible messages for {scene}")
                    first, last, through = store.db.execute(
                        "SELECT MIN(json_extract(body,'$.time')),MAX(json_extract(body,'$.time')),MAX(seq) "
                        "FROM messages WHERE scene=?", (scene,),
                    ).fetchone()
                    recent = store.recent(scene, settings.recent_messages)
                    dates = [datetime.fromtimestamp(value, ZoneInfo(config.timezone)).isoformat()
                             for value in (first, last)]
                    note = template.substitute(messages=report["messages"], first_time=dates[0],
                                               last_time=dates[1], recent=len(recent))
                    store._append(scene, {"role": "user", "content": note})
                    for message in recent:
                        reply = None if message.reply_to is None else store.find_message(scene, message.reply_to)
                        text = render_message(message, timezone=config.timezone, reply=reply)
                        if message.platform_message_id is not None:
                            text += f"（平台消息 ID：{message.platform_message_id}）"
                        if message.reply_to is not None:
                            text += f"（回复平台消息 ID：{message.reply_to}）"
                        store._append(scene, {"role": "user", "content": text})
                    store.db.execute(
                        "INSERT INTO mind_sessions(scene,last_message_seq) VALUES (?,?)", (scene, through)
                    )
                    report.update(first_event_time=dates[0], last_event_time=dates[1], context_messages=len(recent))
                    report["send_states"] = dict(report["send_states"])
    return {"source": str(source), "target": str(target), "backup": str(backup), "scenes": reports}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("History import takes no arguments; use the instance's lenbot.config.json")
    with instance_lock(Path.cwd()):
        print(encode(import_history(load_instance_config(Path.cwd()))))


if __name__ == "__main__":
    main()
