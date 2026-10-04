"""Offline old-message import against content-replaced real event row shapes."""

from __future__ import annotations

from collections import Counter
import copy
import json
from pathlib import Path
import os
import sqlite3

import pytest

from len_bot.next.config import load_instance_config
from len_bot.next.maintenance.import_history import import_history
from len_bot.next.platform.messages import ChatMessage, Segment, Sender
from len_bot.next.storage.store import Store


FIXTURE = Path(__file__).parent / "fixtures" / "next" / "history" / "legacy-redacted.json"
MESSAGE_TYPES = {"GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED", "MESSAGE_SENT", "MESSAGE_SEND_FAILED"}
OTHER = "group:899999"


def _sample() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _source(path: Path, events: list[dict]) -> None:
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE events (id TEXT PRIMARY KEY,event_type TEXT NOT NULL,scene_id TEXT NOT NULL,"
            "actor_id TEXT NOT NULL,timestamp REAL NOT NULL,payload TEXT NOT NULL,metadata TEXT NOT NULL)"
        )
        for event in events:
            db.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                (event["id"], event["event_type"], event["scene_id"], event["actor_id"],
                 event["timestamp"], json.dumps(event["payload"], ensure_ascii=False),
                 json.dumps(event["metadata"], ensure_ascii=False)),
            )


def _instance(root: Path, source: Path, scenes: list[str], *, backup: str = "pre-import.sqlite3") -> Path:
    (root / "persona").mkdir(exist_ok=True)
    config = {"compaction": {"input_tokens": 2000},
        "mode": "isolated-multi", "bot_qq": "900001", "timezone": "UTC",
        "database": "next.sqlite3", "delivery": "simulated",
        "onebot": {"mode": "forward_ws", "ws_url": "ws://127.0.0.1:9"},
        "scenes": {scene: {"persona": "persona", "voice_mode": "direct"} for scene in scenes},
        "models": {
            "providers": {"synthetic": {"api": "openai-chat", "base_url": "http://127.0.0.1:9/v1",
                                        "api_key": "synthetic-only"}},
            "roles": {
                "mind": {"provider": "synthetic", "model": "synthetic-mind",
                         "context_window_tokens": 30000, "max_output_tokens": 200},

            },
        },
        "history_import": {"source": str(source), "backup": backup, "scenes": scenes,
                           "recent_messages": 2},
    }
    (root / "lenbot.config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return root / "next.sqlite3"


def _loaded(root: Path):
    return load_instance_config(root)


def _other_scene(path: Path) -> tuple[list[tuple], list[tuple]]:
    with Store(path) as store:
        store.enqueue(ChatMessage(
            id="synthetic-other", platform="qq", scene=OTHER, platform_message_id="999999",
            sender=Sender(uid="700099", nickname="隔离用户", card=None, role="member"),
            time=1760000000.0, segments=[Segment("text", {"text": "隔离场景原话"})],
            reply_to=None, mentions_bot=False, is_self=False, send_status="received",
        ), {"synthetic": True}, 1760000001.0)
        store.append(OTHER, {"role": "user", "content": "隔离场景上下文"})
        return (
            store.db.execute("SELECT * FROM messages WHERE scene=?", (OTHER,)).fetchall(),
            store.db.execute("SELECT * FROM mind_entries WHERE scene=?", (OTHER,)).fetchall(),
        )


def _target_rows(path: Path, scenes: list[str]) -> tuple[int, int, int, int]:
    with sqlite3.connect(path) as db:
        placeholders = ",".join("?" for _ in scenes)
        return tuple(db.execute(
            f"SELECT COUNT(*) FROM {table} WHERE scene IN ({placeholders})", scenes,
        ).fetchone()[0] for table in ("messages", "mind_entries", "mind_sessions", "turns"))


def test_import_real_redacted_shapes_keeps_original_facts_and_isolation(tmp_path: Path) -> None:
    sample = _sample()
    events = sample["events"]
    scenes = sorted({event["scene_id"] for event in events if event["event_type"] in MESSAGE_TYPES})
    source = tmp_path / "offline-legacy.sqlite3"
    _source(source, events)
    source_before = source.read_bytes()
    target = _instance(tmp_path, source, scenes)
    other_messages, other_entries = _other_scene(target)

    report = import_history(_loaded(tmp_path))
    assert source.read_bytes() == source_before
    assert Path(report["backup"]) == tmp_path / "pre-import.sqlite3"
    assert sum(item["source_messages"] for item in report["scenes"].values()) == 12
    assert sum(item["messages"] for item in report["scenes"].values()) == 11
    assert sum(item["duplicate_events"] for item in report["scenes"].values()) == 1
    assert sum(item["unconverted_events"].get("ACTION_SHADOWED", 0)
               for item in report["scenes"].values()) == 1
    states = Counter(state for item in report["scenes"].values()
                     for state, count in item["send_states"].items() for _ in range(count))
    assert states == {"received": 6, "sent": 2, "failed": 2, "unconfirmed": 1}

    originals = {event["id"]: (number, event) for number, event in enumerate(events, 1)}
    with Store(target) as store:
        message_rows = store.db.execute(
            "SELECT seq,scene,platform_id,body,raw,received_at FROM messages ORDER BY seq"
        ).fetchall()
        imported = [row for row in message_rows if row["scene"] in scenes]
        assert len(imported) == 11
        assert all(row["received_at"] is None for row in imported)
        assert store.db.execute("SELECT COUNT(*) FROM message_search").fetchone()[0] == 12
        assert store.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
        assert store.db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
        assert store.db.execute("SELECT * FROM messages WHERE scene=?", (OTHER,)).fetchall() == other_messages
        assert store.db.execute("SELECT * FROM mind_entries WHERE scene=?", (OTHER,)).fetchall() == other_entries

        observed_ids = []
        duplicate = []
        for row in imported:
            body, raw = json.loads(row["body"]), json.loads(row["raw"])
            original = raw["legacy_events"][0]["event"]
            payload = original["payload"]
            assert body["id"] == original["id"]
            assert body["scene"] == row["scene"]
            assert body["platform_message_id"] == row["platform_id"]
            assert body["time"] == original["timestamp"]
            assert body["sender"]["uid"] == original["actor_id"].removeprefix("user:")
            if original["event_type"] in {"GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED"}:
                assert body["segments"] == payload["segments"]
                assert body["sender"]["nickname"] == payload["sender"]["nickname"]
                assert body["sender"]["card"] == payload["sender"]["card"]
                assert body["sender"]["role"] == payload["sender"]["role"]
                assert body["reply_to"] == payload["reply_to_message_id"]
                assert body["mentions_bot"] == (payload["at_bot"] or payload["reply_bot"])
                assert not body["is_self"]
            else:
                assert body["reply_to"] == payload["reply_to"]
                assert body["is_self"] and body["sender"]["nickname"] is None
                assert [segment["type"] for segment in body["segments"]] == [
                    segment["type"] for segment in payload["segments"]
                ]
                for old_segment, converted in zip(payload["segments"], body["segments"]):
                    if old_segment["type"] == "text":
                        assert converted["data"]["text"] == old_segment["text"]
                    elif old_segment["type"] in {"image", "video", "audio"}:
                        assert converted["data"]["legacy_asset_id"] == old_segment["asset_id"]
            for entry in raw["legacy_events"]:
                source_rowid, source_event = originals[entry["event"]["id"]]
                assert entry == {"rowid": source_rowid, "event": source_event}
                observed_ids.append(source_event["id"])
            if len(raw["legacy_events"]) > 1:
                duplicate.append(raw["legacy_events"])
        assert len(duplicate) == 1 and len(duplicate[0]) == 2
        assert {entry["event"]["id"] for entry in duplicate[0]} == {
            ident for ident, labels in sample["labels"].items()
            if any(label.startswith("duplicate_platform_") for label in labels)
        }
        assert set(observed_ids) == {event["id"] for event in events if event["event_type"] in MESSAGE_TYPES}

        for scene in scenes:
            count = report["scenes"][scene]["messages"]
            assert store.pending_messages(scene) == []
            assert tuple(store.db.execute(
                "SELECT last_message_seq,recap,attention_state FROM mind_sessions WHERE scene=?", (scene,),
            ).fetchone()) == (store.max_message_seq(scene), None, None)
            history = [message for _, message in store.active_history(scene)[1]]
            assert len(history) == 1 + min(2, count)
            assert all(message["role"] == "user" for message in history)
            assert "旧消息" in history[0]["content"] and "旧核心的内部思考" in history[0]["content"]
            matches = store.search_messages(scene, query="替代原话", who=None,
                                            after=None, before=None, snapshot=store.max_message_seq(scene),
                                            offset=0, limit=20)
            if any("替代原话" in json.dumps(json.loads(row["body"])["segments"], ensure_ascii=False)
                   for row in imported if row["scene"] == scene):
                assert matches

    with Store(tmp_path / "pre-import.sqlite3") as backup:
        assert backup.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 1
        assert backup.db.execute("SELECT COUNT(*) FROM mind_entries").fetchone()[0] == 1
        assert backup.db.execute("SELECT * FROM messages WHERE scene=?", (OTHER,)).fetchall() == other_messages
        assert backup.db.execute("SELECT * FROM mind_entries WHERE scene=?", (OTHER,)).fetchall() == other_entries
        assert all(backup.max_message_seq(scene) == 0 for scene in scenes)


@pytest.mark.parametrize("damage", ["conflicting_duplicate", "invalid_payload_json"])
def test_bad_source_rolls_back_entire_import(tmp_path: Path, damage: str) -> None:
    sample = _sample()
    events = sample["events"]
    scenes = sorted({event["scene_id"] for event in events if event["event_type"] in MESSAGE_TYPES})
    source = tmp_path / "offline-legacy.sqlite3"
    _source(source, events)
    if damage == "conflicting_duplicate":
        second = next(ident for ident, labels in sample["labels"].items()
                      if "duplicate_platform_second" in labels)
        with sqlite3.connect(source) as db:
            payload = json.loads(db.execute("SELECT payload FROM events WHERE id=?", (second,)).fetchone()[0])
            payload["raw_text"] += "（合成冲突）"
            db.execute("UPDATE events SET payload=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), second))
        expected_error = "Conflicting platform message"
    else:
        with sqlite3.connect(source) as db:
            db.execute("UPDATE events SET payload=? WHERE rowid=2", ("{synthetic invalid json",))
        expected_error = "Invalid legacy row"
    source_before = source.read_bytes()
    target = _instance(tmp_path, source, scenes)
    other_messages, other_entries = _other_scene(target)

    with pytest.raises(ValueError, match=expected_error):
        import_history(_loaded(tmp_path))
    assert source.read_bytes() == source_before
    assert _target_rows(target, scenes) == (0, 0, 0, 0)
    assert (tmp_path / "pre-import.sqlite3").is_file()
    with Store(target) as store, Store(tmp_path / "pre-import.sqlite3") as backup:
        for db in (store.db, backup.db):
            assert db.execute("SELECT * FROM messages WHERE scene=?", (OTHER,)).fetchall() == other_messages
            assert db.execute("SELECT * FROM mind_entries WHERE scene=?", (OTHER,)).fetchall() == other_entries


def test_reimport_rejected_without_overwriting_existing_backup(tmp_path: Path) -> None:
    sample = _sample()
    events = sample["events"]
    scenes = sorted({event["scene_id"] for event in events if event["event_type"] in MESSAGE_TYPES})
    source = tmp_path / "offline-legacy.sqlite3"
    _source(source, events)
    target = _instance(tmp_path, source, scenes)
    import_history(_loaded(tmp_path))
    backup = tmp_path / "pre-import.sqlite3"
    backup_before = backup.read_bytes()
    with pytest.raises(FileExistsError, match="backup already exists"):
        import_history(_loaded(tmp_path))
    assert backup.read_bytes() == backup_before

    config_path = tmp_path / "lenbot.config.json"
    config = json.loads(config_path.read_text())
    config["history_import"]["backup"] = "second-backup.sqlite3"
    config_path.write_text(json.dumps(config, ensure_ascii=False))
    with pytest.raises(ValueError, match="empty target scene"):
        import_history(_loaded(tmp_path))
    assert not (tmp_path / "second-backup.sqlite3").exists()
    assert backup.read_bytes() == backup_before
    assert sum(_target_rows(target, scenes)) > 0


@pytest.mark.parametrize("guard", ["same_inode", "nonempty_wal"])
def test_source_is_not_the_target_or_a_live_wal_snapshot(tmp_path: Path, guard: str) -> None:
    sample = _sample()
    source = tmp_path / "offline-legacy.sqlite3"
    _source(source, sample["events"])
    scenes = sorted({event["scene_id"] for event in sample["events"] if event["event_type"] in MESSAGE_TYPES})
    target = _instance(tmp_path, source, scenes)
    with Store(target):
        pass
    if guard == "same_inode":
        source.unlink()
        os.link(target, source)
        expected_error = "same file"
    else:
        Path(str(source) + "-wal").write_bytes(b"synthetic nonempty WAL")
        expected_error = "nonempty -wal"
    target_before = target.read_bytes()
    with pytest.raises(ValueError, match=expected_error):
        import_history(_loaded(tmp_path))
    assert target.read_bytes() == target_before
    assert not (tmp_path / "pre-import.sqlite3").exists()


@pytest.mark.parametrize("shape", ["incoming_unknown", "simulated_send", "unconfirmed_with_id"])
def test_explicitly_synthetic_missing_shapes_keep_unknowns(tmp_path: Path, shape: str) -> None:
    """These mutations are not claimed to be captured rows from the named backup."""
    sample = _sample()
    if shape == "incoming_unknown":
        event = copy.deepcopy(next(event for event in sample["events"]
                                   if event["event_type"] == "GROUP_MESSAGE_RECEIVED"))
        event["payload"]["sender"]["nickname"] = None
        event["payload"]["segments"] = None
        expected_status, expected_id = "received", str(event["payload"]["message_id"])
    else:
        event = copy.deepcopy(next(event for event in sample["events"]
                                   if "sent_text" in sample["labels"][event["id"]]))
        event["payload"]["segments"] = None
        if shape == "simulated_send":
            event["metadata"]["simulated"] = True
            event["payload"]["origin_mode"] = "simulated"
            expected_status, expected_id = "simulated", None
        else:
            del event["payload"]["delivery_status"]
            expected_status, expected_id = "unconfirmed", str(event["payload"]["message_id"])
    source = tmp_path / "offline-legacy.sqlite3"
    _source(source, [event])
    scene = event["scene_id"]
    target = _instance(tmp_path, source, [scene])
    report = import_history(_loaded(tmp_path))
    with Store(target) as store:
        row = store.db.execute("SELECT seq,body,raw FROM messages WHERE scene=?", (scene,)).fetchone()
        message = store.read_message(scene, row["seq"])
        assert message.id == event["id"]
        assert message.send_status == expected_status
        assert message.platform_message_id == expected_id
        assert message.sender.nickname is None
        assert message.segments == [Segment("text", {"text": event["payload"]["raw_text"],
                                                     "legacy_text_only": True})]
        assert json.loads(row["raw"])["legacy_events"][0]["event"] == event
        assert store.pending_messages(scene) == []
        assert report["scenes"][scene]["text_only_records"] == 1


@pytest.mark.parametrize("status", ["not_sent", "rejected", "unknown"])
def test_synthetic_simulated_failure_keeps_actual_result(tmp_path: Path, status: str) -> None:
    """The old queue can fail in simulated mode; this combination is synthetic."""
    sample = _sample()
    event = copy.deepcopy(next(event for event in sample["events"]
                               if f"failed_{status}" in sample["labels"][event["id"]]))
    event["metadata"]["simulated"] = True
    event["payload"]["origin_mode"] = "simulated"
    event["payload"]["message_id"] = "synthetic-pseudo-receipt"
    scene = event["scene_id"]
    source = tmp_path / "offline-legacy.sqlite3"
    _source(source, [event])
    target = _instance(tmp_path, source, [scene])

    report = import_history(_loaded(tmp_path))

    expected = "unconfirmed" if status == "unknown" else "failed"
    with Store(target) as store:
        message = store.recent(scene)[0]
        assert message.send_status == expected
        assert message.platform_message_id is None
        row = store.db.execute("SELECT raw FROM messages WHERE scene=?", (scene,)).fetchone()
        assert json.loads(row["raw"])["legacy_events"][0]["event"] == event
    assert report["scenes"][scene]["send_states"] == {expected: 1}
