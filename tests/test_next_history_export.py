"""Offline reverse-message export using redacted old row shapes and synthetic new facts."""

from __future__ import annotations

from dataclasses import asdict, replace
from contextlib import closing
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3

import pytest
from PIL import Image

from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.next.config import load_instance_config
from len_bot.next.import_history import import_history
from len_bot.next.messages import ChatMessage, Segment, Sender
from len_bot.next.store import ImageAsset, Store, WebPage
from len_bot.scenes.models import SceneSession
from len_bot.scenes.reducer import SceneReducer
from len_bot.scheduler.models import TaskItem


FIXTURE = Path(__file__).parent / "fixtures" / "next" / "history" / "legacy-redacted.json"
BOT = "900001"
OTHER = "group:899999"


def _sample() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _scenes(sample: dict) -> list[str]:
    return sorted({event["scene_id"] for event in sample["events"]
                   if event["event_type"] in {"GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED",
                                               "MESSAGE_SENT", "MESSAGE_SEND_FAILED"}})


def _root_config(root: Path, target: Path, scenes: list[str], *, importing: bool) -> None:
    (root / "persona").mkdir(exist_ok=True)
    config = {
        "mode": "isolated-multi", "bot_qq": BOT, "timezone": "UTC",
        "database": "next.sqlite3", "delivery": "simulated",
        "onebot": {"mode": "forward_ws", "ws_url": "ws://127.0.0.1:9"},
        "scenes": {scene: {"persona": "persona", "voice_mode": "direct"} for scene in scenes},
        "models": {
            "providers": {"synthetic": {"api": "openai-chat", "base_url": "http://127.0.0.1:9/v1",
                                        "api_key": "synthetic-only"}},
            "roles": {
                "mind": {"provider": "synthetic", "model": "synthetic-mind",
                         "context_window_tokens": 30000, "max_output_tokens": 200},
                "voice": {"provider": "synthetic", "model": "synthetic-voice",
                          "context_window_tokens": 30000, "max_output_tokens": 200},
            },
        },
    }
    if importing:
        config["history_import"] = {"source": str(target), "backup": "next-before-import.sqlite3",
                                    "scenes": scenes, "recent_messages": 2}
    else:
        config["history_export"] = {"target": "legacy.sqlite3", "backup": "legacy-before-export.sqlite3",
                                    "scenes": scenes}
    (root / "lenbot.config.json").write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")


def _event_row(db: sqlite3.Connection, event: Event) -> int:
    cursor = db.execute(
        "INSERT INTO events(id,event_type,scene_id,actor_id,timestamp,payload,metadata) "
        "VALUES (?,?,?,?,?,?,?)",
        (event.id, event.event_type.value, event.scene_id, event.actor_id, event.timestamp,
         json.dumps(event.payload, ensure_ascii=False), json.dumps(event.metadata, ensure_ascii=False)),
    )
    if event.raw_text:
        db.execute("INSERT INTO events_fts(event_id,scene_id,actor_id,content) VALUES (?,?,?,?)",
                   (event.id, event.scene_id, event.actor_id, event.raw_text))
    return cursor.lastrowid


def _session_row(db: sqlite3.Connection, state: SceneSession) -> None:
    body = state.model_dump(mode="json")
    db.execute(
        "INSERT INTO scene_sessions(scene_id,version,last_observed_event_rowid,"
        "attention_scanned_event_rowid,state_json,updated_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(scene_id) DO UPDATE SET version=excluded.version,"
        "last_observed_event_rowid=excluded.last_observed_event_rowid,"
        "attention_scanned_event_rowid=excluded.attention_scanned_event_rowid,"
        "state_json=excluded.state_json,updated_at=excluded.updated_at",
        (state.scene_id, state.version, state.last_observed_event_rowid,
         state.attention_scanned_event_rowid, json.dumps(body, ensure_ascii=False), 1760001000.0),
    )


async def _old_target(path: Path, sample: dict) -> None:
    old = EventStore(str(path))
    await old.initialize()
    await old.save_task(TaskItem(id="synthetic-old-task", scene_id=_scenes(sample)[0],
                                 description="合成未完成事项", due_at=1800000000.0,
                                 source_event_id="synthetic-task-source"))
    await old.close()
    states: dict[str, SceneSession] = {}
    with closing(sqlite3.connect(path)) as db, db:
        for item in sample["events"]:
            event = Event.model_validate(item)
            rowid = _event_row(db, event)
            state = SceneReducer.reduce(states.get(event.scene_id), event, f"user:{BOT}")
            state.last_observed_event_rowid = rowid
            state.attention_scanned_event_rowid = rowid
            states[event.scene_id] = state
        # This separately routed scene must survive the export untouched.
        other = Event(id="synthetic-other-event", event_type=EventType.OPERATOR_ACTION,
                      scene_id=OTHER, actor_id="operator:synthetic", timestamp=1760002000.0,
                      payload={"note": "合成其他场景"})
        rowid = _event_row(db, other)
        state = SceneReducer.reduce(None, other, f"user:{BOT}")
        state.last_observed_event_rowid = state.attention_scanned_event_rowid = rowid
        states[OTHER] = state
        for state in states.values():
            _session_row(db, state)
        assert db.execute("SELECT COUNT(*) FROM pending_runtime_events").fetchone()[0] == 0


def _old_rows(path: Path) -> dict[str, list[tuple]]:
    with closing(sqlite3.connect(path)) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("events", "events_fts", "scene_sessions", "tasks")}


def _jpeg() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(stream, format="JPEG")
    return stream.getvalue()


def _new_incoming(scene: str, platform_id: str, *, group: bool, reply_to: str | None) -> tuple[ChatMessage, dict]:
    uid = "700099" if group else scene.removeprefix("private:")
    segments = []
    if reply_to is not None:
        segments.append(Segment("reply", {"id": reply_to}))
    if group:
        segments.append(Segment("at", {"qq": BOT}))
    segments.append(Segment("text", {"text": "合成新期间收件"}))
    message = ChatMessage(
        id="new-group-input" if group else "new-private-input", platform="qq", scene=scene,
        platform_message_id=platform_id, sender=Sender(uid, "合成用户", None, "member" if group else None),
        time=1800000100.0 if group else 1800000101.0, segments=segments, reply_to=reply_to,
        mentions_bot=group, is_self=False, send_status="received",
    )
    raw = {
        "post_type": "message", "message_type": "group" if group else "private",
        "self_id": int(BOT), "user_id": int(uid), "message_id": platform_id,
        "time": message.time, "sender": {"nickname": "合成用户", "card": "", "role": message.sender.role},
        "message": [asdict(segment) for segment in segments],
        "raw_message": "合成原始平台文本",
    }
    if group:
        raw["group_id"] = int(scene.removeprefix("group:"))
    return message, raw


def _outgoing(scene: str, ident: str, status: str, platform_id: str | None, at: float,
              *, reply_to: str | None = None) -> ChatMessage:
    segments = [Segment("text", {"text": f"合成新期间{ident}原话"})]
    if status == "sent":
        segments.append(Segment("at", {"qq": "700099"}))
    return ChatMessage(
        id=ident, platform="qq", scene=scene, platform_message_id=platform_id,
        sender=Sender(BOT, None, None, None), time=at, segments=segments,
        reply_to=reply_to, mentions_bot=False, is_self=True, send_status=status,
    )


def _add_new_messages(path: Path, sample: dict) -> dict[str, dict]:
    parent_id = next(ident for ident, names in sample["labels"].items()
                     if "linked_sent_parent" in names)
    parent = next(event for event in sample["events"] if event["id"] == parent_id)
    group = parent["scene_id"]
    private = next(event["scene_id"] for event in sample["events"]
                   if event["event_type"] == "PRIVATE_MESSAGE_RECEIVED")
    added = {}
    with Store(path) as store:
        for message, raw in (
            _new_incoming(group, "synthetic-cross-scene", group=True,
                          reply_to=str(parent["payload"]["message_id"])),
            _new_incoming(private, "synthetic-cross-scene", group=False, reply_to=None),
        ):
            seq = store.enqueue(message, raw, message.time + 0.5)
            turn = store.start_turn(message.scene, batch=(seq, ["合成已合并入站"]), attention_state=None)
            store.end_turn(turn, "settled")
            added[message.id] = {"scene": message.scene, "seq": seq}
        for message in (
            _outgoing(group, "new-confirmed", "sent", "synthetic-sent-id", 1800000200.0,
                      reply_to="synthetic-cross-scene"),
            _outgoing(group, "new-failed", "failed", None, 1800000201.0),
            _outgoing(group, "new-unknown", "unconfirmed", None, 1800000202.0),
            _outgoing(group, "new-simulated", "simulated", None, 1800000203.0),
        ):
            entry = store.prepare_expression(group, f"synthetic-{message.id}", "合成表达结果")
            provisional = replace(message, platform_message_id=None, send_status="unconfirmed")
            seq = store.start_expression_part(entry, provisional, "合成发送进行中")
            store.finish_expression((seq, entry), message, "合成发送已记录")
            added[message.id] = {"scene": group, "seq": seq}
        store.create_schedule(group, due_at=1800001000.0, timezone="UTC", note="合成安排",
                              target="self", requester=None, limit=50)
        store.save_web_page(group, WebPage("https://fixture.invalid/page", "https://fixture.invalid/page",
                                           1800000000.0, "text/plain", "合成网页", ""))
        store.save_image(group, "synthetic-cross-scene", 1,
                         ImageAsset(_jpeg(), 2, 2, False, 1800000000.0))
        for ident, item in added.items():
            row = store.db.execute("SELECT body,raw,received_at FROM messages WHERE seq=?", (item["seq"],)).fetchone()
            item.update(body=json.loads(row[0]), raw=None if row[1] is None else json.loads(row[1]),
                        received_at=row[2])
    return added


async def _prepared(tmp_path: Path) -> tuple[dict, list[str], Path, Path, dict[str, dict]]:
    sample = _sample()
    scenes = _scenes(sample)
    old_path = tmp_path / "legacy.sqlite3"
    await _old_target(old_path, sample)
    _root_config(tmp_path, old_path, scenes, importing=True)
    import_history(load_instance_config(tmp_path))
    new_path = tmp_path / "next.sqlite3"
    added = _add_new_messages(new_path, sample)
    _root_config(tmp_path, old_path, scenes, importing=False)
    return sample, scenes, old_path, new_path, added


@pytest.mark.asyncio
async def test_export_appends_only_new_facts_and_updates_real_legacy_reads(tmp_path: Path) -> None:
    from len_bot.next.export_history import export_history

    sample, scenes, old_path, new_path, added = await _prepared(tmp_path)
    old_before = _old_rows(old_path)
    new_before = new_path.read_bytes()
    with closing(sqlite3.connect(old_path)) as db:
        old_cutoffs = {scene: db.execute(
            "SELECT MAX(rowid) FROM events WHERE scene_id=?", (scene,)).fetchone()[0]
                       for scene in scenes}
    report = export_history(load_instance_config(tmp_path))

    assert new_path.read_bytes() == new_before
    assert sum(value["source_messages"] for value in report["scenes"].values()) == 17
    assert sum(value["messages"] for value in report["scenes"].values()) == 6
    assert sum(value["legacy_messages"] for value in report["scenes"].values()) == 11
    assert sum(value["already_exported"] for value in report["scenes"].values()) == 0
    assert {status: sum(value["send_states"].get(status, 0) for value in report["scenes"].values())
            for status in ("received", "sent", "failed", "unconfirmed", "simulated")} == {
                "received": 2, "sent": 1, "failed": 1, "unconfirmed": 1, "simulated": 1,
            }
    assert sum(value["retained_new_data"]["web_documents"] for value in report["scenes"].values()) == 1
    assert sum(value["retained_new_data"]["image_cache"] for value in report["scenes"].values()) == 1
    assert sum(value["retained_new_data"]["schedules"].get("pending", 0)
               for value in report["scenes"].values()) == 1
    assert sum(value["legacy_open_tasks"].get("pending", 0)
               for value in report["scenes"].values()) == 1

    with closing(sqlite3.connect(old_path)) as db:
        db.row_factory = sqlite3.Row
        old_ids = {event["id"] for event in sample["events"]}
        actual_ids = {row[0] for row in db.execute("SELECT id FROM events")}
        assert old_ids <= actual_ids and set(added) <= actual_ids
        for ident, source in added.items():
            row = db.execute("SELECT scene_id,actor_id,event_type,payload,metadata,rowid FROM events WHERE id=?",
                             (ident,)).fetchone()
            metadata = json.loads(row["metadata"])
            assert row["scene_id"] == source["scene"]
            assert metadata["next_message"] == {
                "seq": source["seq"], "body": source["body"], "raw": source["raw"],
                "received_at": source["received_at"],
            }
            if source["body"]["is_self"]:
                assert row["actor_id"] == f"user:{BOT}"
            else:
                assert row["actor_id"] == "user:" + source["body"]["sender"]["uid"]
            assert row["rowid"] > old_cutoffs[row["scene_id"]]
        group_event = db.execute("SELECT event_type,payload FROM events WHERE id='new-group-input'").fetchone()
        group_payload = json.loads(group_event["payload"])
        assert group_event["event_type"] == "GROUP_MESSAGE_RECEIVED"
        assert group_payload["at_bot"] is True and group_payload["reply_bot"] is True
        assert group_payload["segments"] == [asdict(segment) for segment in
                                              _new_incoming(added["new-group-input"]["scene"],
                                                            "synthetic-cross-scene", group=True,
                                                            reply_to=group_payload["reply_to_message_id"])[0].segments]
        assert db.execute("SELECT event_type FROM events WHERE id='new-private-input'").fetchone()[0] == (
            "PRIVATE_MESSAGE_RECEIVED"
        )
        outbound = {
            ident: db.execute("SELECT event_type,payload,metadata FROM events WHERE id=?", (ident,)).fetchone()
            for ident in ("new-confirmed", "new-failed", "new-unknown", "new-simulated")
        }
        confirmed = json.loads(outbound["new-confirmed"]["payload"])
        assert outbound["new-confirmed"]["event_type"] == "MESSAGE_SENT"
        assert confirmed["delivery_status"] == "sent"
        assert confirmed["message_id"] == "synthetic-sent-id"
        assert confirmed["segments"][-1] == {"type": "at", "qq_uid": "700099"}
        assert not json.loads(outbound["new-confirmed"]["metadata"]).get("conversation_excluded", False)
        for ident, status, unknown in (("new-failed", "not_sent", False),
                                       ("new-unknown", "unknown", True)):
            row = outbound[ident]
            payload = json.loads(row["payload"])
            assert row["event_type"] == "MESSAGE_SEND_FAILED"
            assert payload["delivery_status"] == status and payload["delivery_unknown"] is unknown
            assert payload["attempted_text"] == payload["raw_text"]
            assert payload["message_id"] is None
        simulated = json.loads(outbound["new-simulated"]["payload"])
        simulated_meta = json.loads(outbound["new-simulated"]["metadata"])
        assert outbound["new-simulated"]["event_type"] == "MESSAGE_SENT"
        assert simulated["delivery_status"] == "sent" and simulated["origin_mode"] == "simulated"
        assert simulated["message_id"] is None
        assert simulated_meta["simulated"] is True and simulated_meta["conversation_excluded"] is True
        assert db.execute("SELECT COUNT(*) FROM events_fts WHERE event_id IN ("
                          "'new-group-input','new-private-input','new-confirmed','new-failed',"
                          "'new-unknown','new-simulated')").fetchone()[0] == len(added)
        for scene in scenes:
            session = db.execute(
                "SELECT last_observed_event_rowid,attention_scanned_event_rowid,state_json "
                "FROM scene_sessions WHERE scene_id=?", (scene,),
            ).fetchone()
            state = json.loads(session["state_json"])
            latest = db.execute("SELECT MAX(rowid) FROM events WHERE scene_id=?", (scene,)).fetchone()[0]
            assert session["last_observed_event_rowid"] == latest == session["attention_scanned_event_rowid"]
            assert state["last_observed_event_rowid"] == latest == state["attention_scanned_event_rowid"]
            assert state["pending_wakes"] == []
        assert db.execute("SELECT COUNT(*) FROM pending_runtime_events").fetchone()[0] == 0
        assert db.execute("SELECT * FROM events WHERE id='synthetic-other-event'").fetchone() is not None
    old_after = _old_rows(old_path)
    assert old_after["events"][:len(old_before["events"])] == old_before["events"]
    assert old_after["events_fts"][:len(old_before["events_fts"])] == old_before["events_fts"]
    assert [row for row in old_after["scene_sessions"] if row[0] == OTHER] == [
        row for row in old_before["scene_sessions"] if row[0] == OTHER]
    assert old_after["tasks"] == old_before["tasks"]
    assert _old_rows(tmp_path / "legacy-before-export.sqlite3") == old_before

    old = EventStore(str(old_path))
    await old.initialize()
    try:
        group = added["new-group-input"]["scene"]
        session = await old.load_scene_session(group)
        observed = await old.get_events_since(group, old_cutoffs[group], limit=20)
        assert {event.id for event in observed} == {
            ident for ident, item in added.items() if item["scene"] == group}
        conversation = await old.get_events_since(group, old_cutoffs[group], limit=20,
                                                  conversation_only=True)
        assert "new-confirmed" in {event.id for event in conversation}
        assert "new-simulated" not in {event.id for event in conversation}
        timeline = await old.query_timeline(group, 1800000000.0, 1800001000.0,
                                            [group], limit=20,
                                            through_rowid=session["last_observed_event_rowid"])
        assert "new-group-input" in {event["id"] for event in timeline}
        assert session["participants"]["user:700099"]["nickname"] == "合成用户"
        assert session["last_bot_message_event_id"] == "new-confirmed"
    finally:
        await old.close()

    config_file = tmp_path / "lenbot.config.json"
    config = json.loads(config_file.read_text())
    config["history_export"]["backup"] = "legacy-before-export-again.sqlite3"
    config_file.write_text(json.dumps(config, ensure_ascii=False))
    second = export_history(load_instance_config(tmp_path))
    assert sum(value["messages"] for value in second["scenes"].values()) == 0
    assert sum(value["already_exported"] for value in second["scenes"].values()) == len(added)
    assert _old_rows(old_path) == old_after


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["event_id", "platform_id", "broken_new_body"])
async def test_export_conflicts_and_invalid_source_roll_back_every_scene(tmp_path: Path, problem: str) -> None:
    from len_bot.next.export_history import export_history

    _, scenes, old_path, new_path, added = await _prepared(tmp_path)
    group = added["new-confirmed"]["scene"]
    if problem == "broken_new_body":
        with closing(sqlite3.connect(new_path)) as db, db:
            db.execute("UPDATE messages SET body=? WHERE seq=?",
                       ("{synthetic invalid JSON", added["new-simulated"]["seq"]))
    else:
        conflicting = Event(
            id="new-simulated" if problem == "event_id" else "synthetic-platform-conflict",
            event_type=EventType.OPERATOR_ACTION if problem == "event_id" else EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=group, actor_id="operator:synthetic" if problem == "event_id" else "user:700099",
            timestamp=1799999999.0,
            payload={"raw_text": "合成旧侧冲突"} if problem == "event_id" else {
                "raw_text": "合成旧侧冲突", "message_id": "synthetic-sent-id",
                "sender": {"nickname": "合成用户", "card": None, "role": "member"},
                "segments": [{"type": "text", "data": {"text": "合成旧侧冲突"}}],
                "at_bot": False, "reply_bot": False, "reply_to_message_id": None,
            },
        )
        with closing(sqlite3.connect(old_path)) as db, db:
            rowid = _event_row(db, conflicting)
            row = db.execute("SELECT state_json FROM scene_sessions WHERE scene_id=?", (group,)).fetchone()
            state = SceneReducer.reduce(SceneSession.model_validate(json.loads(row[0])),
                                        conflicting, f"user:{BOT}")
            state.last_observed_event_rowid = state.attention_scanned_event_rowid = rowid
            _session_row(db, state)
    before = _old_rows(old_path)
    source_before = new_path.read_bytes()
    with pytest.raises(ValueError):
        export_history(load_instance_config(tmp_path))
    assert _old_rows(old_path) == before
    assert new_path.read_bytes() == source_before
    assert (tmp_path / "legacy-before-export.sqlite3").exists()
    assert _old_rows(tmp_path / "legacy-before-export.sqlite3") == before


@pytest.mark.asyncio
@pytest.mark.parametrize("problem", ["pending_wake", "pending_runtime", "cursor_gap", "cursor_mismatch"])
async def test_export_requires_finished_legacy_observation(tmp_path: Path, problem: str) -> None:
    from len_bot.next.export_history import export_history

    _, scenes, old_path, new_path, added = await _prepared(tmp_path)
    group = added["new-group-input"]["scene"]
    with closing(sqlite3.connect(old_path)) as db, db:
        row = db.execute("SELECT last_observed_event_rowid,attention_scanned_event_rowid,state_json "
                         "FROM scene_sessions WHERE scene_id=?", (group,)).fetchone()
        state = json.loads(row[2])
        if problem == "pending_wake":
            state["pending_wakes"] = [{"event_id": "synthetic-pending", "rowid": row[0],
                                       "actor_id": "user:700099", "reasons": ["direct"],
                                       "certain": True, "created_at": 1800000000.0}]
            db.execute("UPDATE scene_sessions SET state_json=? WHERE scene_id=?",
                       (json.dumps(state, ensure_ascii=False), group))
        elif problem == "pending_runtime":
            db.execute("INSERT INTO pending_runtime_events VALUES (?,?,?)",
                       ("synthetic-pending", group, Event(
                           id="synthetic-pending", event_type=EventType.OPERATOR_ACTION,
                           scene_id=group, actor_id="operator:synthetic",
                           payload={"note": "合成未完成输入"}).model_dump_json()))
        elif problem == "cursor_gap":
            state["last_observed_event_rowid"] = state["attention_scanned_event_rowid"] = row[0] - 1
            db.execute("UPDATE scene_sessions SET last_observed_event_rowid=?,"
                       "attention_scanned_event_rowid=?,state_json=? WHERE scene_id=?",
                       (row[0] - 1, row[0] - 1, json.dumps(state, ensure_ascii=False), group))
        else:
            state["last_observed_event_rowid"] = row[0] - 1
            db.execute("UPDATE scene_sessions SET state_json=? WHERE scene_id=?",
                       (json.dumps(state, ensure_ascii=False), group))
    before = _old_rows(old_path)
    with pytest.raises(ValueError):
        export_history(load_instance_config(tmp_path))
    assert _old_rows(old_path) == before
    assert new_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", ["existing_backup", "same_inode", "source_wal", "target_wal"])
async def test_export_rejects_unsafe_offline_files_without_changing_data(tmp_path: Path, guard: str) -> None:
    from len_bot.next.export_history import export_history

    _, _, old_path, new_path, _ = await _prepared(tmp_path)
    source_before, old_before = new_path.read_bytes(), old_path.read_bytes()
    backup = tmp_path / "legacy-before-export.sqlite3"
    if guard == "existing_backup":
        backup.write_bytes(b"synthetic existing backup")
        expected_error = "backup already exists"
    elif guard == "same_inode":
        link = tmp_path / "same-inode.sqlite3"
        os.link(new_path, link)
        config_path = tmp_path / "lenbot.config.json"
        config = json.loads(config_path.read_text())
        config["history_export"]["target"] = link.name
        config_path.write_text(json.dumps(config, ensure_ascii=False))
        expected_error = "same file"
    else:
        sidecar_of = new_path if guard == "source_wal" else old_path
        Path(str(sidecar_of) + "-wal").write_bytes(b"synthetic nonempty WAL")
        expected_error = "nonempty -wal"

    with pytest.raises((ValueError, FileExistsError), match=expected_error):
        export_history(load_instance_config(tmp_path))
    assert new_path.read_bytes() == source_before
    assert old_path.read_bytes() == old_before
    if guard == "existing_backup":
        assert backup.read_bytes() == b"synthetic existing backup"
    else:
        assert not backup.exists()


@pytest.mark.asyncio
async def test_export_requires_current_format_without_modifying_legacy_target(tmp_path: Path) -> None:
    from len_bot.next.export_history import export_history
    from len_bot.next.store import FORMAT_VERSION

    _, _, old_path, new_path, _ = await _prepared(tmp_path)
    shutil.copyfile(
        Path(__file__).parent / "fixtures" / "next" / "migration" / "v12-synthetic.sqlite3",
        new_path,
    )
    source_before, target_before = new_path.read_bytes(), old_path.read_bytes()
    with closing(sqlite3.connect(new_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12

    with pytest.raises(ValueError, match=f"requires current next-core database format {FORMAT_VERSION}"):
        export_history(load_instance_config(tmp_path))

    assert new_path.read_bytes() == source_before
    assert old_path.read_bytes() == target_before
    assert not (tmp_path / "legacy-before-export.sqlite3").exists()
