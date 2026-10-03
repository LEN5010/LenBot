"""Offline format upgrades using committed DDL with synthetic, content-replaced rows."""

from dataclasses import asdict
import json
from io import BytesIO
from pathlib import Path
import shutil
import sqlite3

import pytest
from PIL import Image

from len_bot.next.migrate import migrate_database
from len_bot.next.learning_store import LearningStore
from len_bot.next.messages import parse_message, plain_text
from len_bot.next.store import FORMAT_VERSION, Store
from len_bot.next.tasks_store import TaskStore
from len_bot.next.plugin_store import PluginStore
from len_bot.next.schedule_store import ScheduleStore


FIXTURES = Path(__file__).parent / "fixtures" / "next" / "migration"
MESSAGE_COLUMNS = ("seq", "scene", "platform_id", "body", "raw")
MODEL_CALL_COLUMNS = (
    "id", "turn_id", "role", "started", "ended", "request", "response", "usage", "error",
)
TURN_COLUMNS = ("id", "scene", "started", "ended", "status", "error")
SCHEDULE_COLUMNS = (
    "id", "scene", "created", "due_at", "timezone", "note", "target",
    "requester", "status", "delivered_at", "reason",
)
SCHEDULE_V15_COLUMNS = (*SCHEDULE_COLUMNS, "interval_seconds")
FIRST_EXPRESSION_COLUMNS = (
    "wake_received_at", "first_expression_at", "first_expression_delivery",
)
OLD_MEDIA_COLUMNS = (
    "id", "persona_id", "file", "mime_type", "width", "height", "animated", "data",
)
V4_ATTENTION = {
    "focus_started_at": 1789000010.0,
    "last_contact_at": 1789000020.0,
    "ambient_last_at": 1789000030.0,
    "silence_level": 2,
    "keyword_last": {"合成词": 1789000040.0},
    "pending": {"channel": "named", "first_at": 1789000050.0,
                "keywords": ["合成词"], "score": None},
}
V6_ATTENTION = {**V4_ATTENTION, "quiet_notice_until": None}


TASK_V28_COLUMNS = "id,scene,requester,goal,deliverable,context,input,status,created,started,ended,container,question,summary,error"


def _historical_columns(table: str, db: sqlite3.Connection) -> str:
    if table == "tasks":
        return TASK_V28_COLUMNS
    # These additions have no source values in the historical fixtures.
    return ",".join(row[1] for row in db.execute(f"PRAGMA table_info({table})")
                    if (table, row[1]) not in {("messages", "persona_id"),
                                               ("schedules", "legacy_source"),
                                               ("model_calls", "scene"), ("model_calls", "plugin")})


def _remove_browser_columns(db) -> None:
    for field in ("account_browser", "browser_active", "browser_session"):
        db.execute(f"ALTER TABLE tasks DROP COLUMN {field}")


def _rows(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as db:
        message_columns = MESSAGE_COLUMNS + (("received_at",) if any(
            row[1] == "received_at" for row in db.execute("PRAGMA table_info(messages)")) else ())
        return {
            "messages": db.execute(
                f"SELECT {','.join(message_columns)} FROM messages ORDER BY seq"
            ).fetchall(),
            "mind_entries": db.execute("SELECT * FROM mind_entries ORDER BY seq").fetchall(),
            "turns": db.execute(
                f"SELECT {','.join(TURN_COLUMNS)} FROM turns ORDER BY rowid"
            ).fetchall(),
            "model_calls": db.execute(
                f"SELECT {','.join(MODEL_CALL_COLUMNS)} FROM model_calls ORDER BY id"
            ).fetchall(),
        }


def _version(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as db:
        return (db.execute("PRAGMA application_id").fetchone()[0],
                db.execute("PRAGMA user_version").fetchone()[0])


def _daily_cron_rows(rows: list[tuple]) -> list[tuple]:
    """Format 24 keeps each schedule row and turns its daily minute into full cron text."""
    return [(*row[:-1], None if row[-1] is None else f"cron:{row[-1] % 60} {row[-1] // 60} * * *")
            for row in rows]


def _old_columns(path: Path) -> dict[str, list[tuple]]:
    rows = _rows(path)
    with sqlite3.connect(path) as db:
        rows["messages"] = db.execute(
            f"SELECT {','.join(MESSAGE_COLUMNS)} FROM messages ORDER BY seq"
        ).fetchall()
    return rows


@pytest.mark.parametrize("format_number", [1, 2, 3, 4, 5, 6])
def test_old_format_upgrades_without_changing_original_records(tmp_path: Path, format_number: int) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / f"v{format_number}-synthetic.sqlite3", path)
    before = _old_columns(path)
    assert _version(path) == (0x4C424E31, format_number)
    with pytest.raises(ValueError, match=f"format {format_number} requires offline migration"):
        Store(path)

    original_backup = migrate_database(path)
    assert original_backup == tmp_path / f"isolated.sqlite3.v{format_number}.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(original_backup) == (0x4C424E31, format_number)
    for intermediate_format in range(format_number + 1, FORMAT_VERSION):
        assert _version(tmp_path / f"isolated.sqlite3.v{intermediate_format}.bak") == (
            0x4C424E31, intermediate_format
        )
    assert _old_columns(path) == before == _old_columns(original_backup)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM model_calls WHERE mind_entry_seq IS NOT NULL").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM model_calls WHERE cost IS NOT NULL").fetchone()[0] == 0
        assert db.execute(
            f"SELECT {','.join(FIRST_EXPRESSION_COLUMNS)} FROM turns ORDER BY rowid"
        ).fetchall() == [(None, None, None)] * db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        assert db.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM schedules WHERE interval_seconds IS NOT NULL").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM schedules WHERE cron IS NOT NULL").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM web_documents").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM image_cache").fetchone()[0] == 0
        assert [row[0] for row in db.execute(
            "SELECT discovered_tools FROM mind_sessions ORDER BY scene"
        )] == ["[]"] * db.execute("SELECT COUNT(*) FROM mind_sessions").fetchone()[0]
        if format_number < 4:
            assert db.execute("SELECT count(*) FROM mind_sessions WHERE attention_state IS NOT NULL").fetchone()[0] == 0
        if format_number < 3:
            assert db.execute("SELECT count(*) FROM messages WHERE received_at IS NOT NULL").fetchone()[0] == 0
        if format_number < 5:
            assert db.execute("SELECT count(*) FROM mind_sessions WHERE attention_state IS NOT NULL").fetchone()[0] == (1 if format_number == 4 else 0)
    if format_number == 1:
        intermediate = tmp_path / "isolated.sqlite3.v2.bak"
        assert _version(intermediate) == (0x4C424E31, 2)
        assert _old_columns(intermediate) == before
        assert _version(tmp_path / "isolated.sqlite3.v3.bak") == (0x4C424E31, 3)
        assert _version(tmp_path / "isolated.sqlite3.v4.bak") == (0x4C424E31, 4)
        assert _version(tmp_path / "isolated.sqlite3.v5.bak") == (0x4C424E31, 5)
        assert _version(tmp_path / "isolated.sqlite3.v6.bak") == (0x4C424E31, 6)
        with Store(path) as store:
            assert store.pending_messages("group:12345") == []
            assert store.load_attention("group:12345") is None
            assert store.active_history("group:12345") == (
                None, [(row[0], json.loads(row[2])) for row in before["mind_entries"]]
            )
    elif format_number == 2:
        assert _version(tmp_path / "isolated.sqlite3.v3.bak") == (0x4C424E31, 3)
        assert _version(tmp_path / "isolated.sqlite3.v4.bak") == (0x4C424E31, 4)
        assert _version(tmp_path / "isolated.sqlite3.v5.bak") == (0x4C424E31, 5)
        assert _version(tmp_path / "isolated.sqlite3.v6.bak") == (0x4C424E31, 6)
        with Store(path) as store:
            assert store.pending_messages("group:12345") == []
            assert store.pending_messages("private:67890") == []
            group_entries = [(row[0], json.loads(row[2])) for row in before["mind_entries"]
                             if row[1] == "group:12345" and row[0] > 1]
            assert store.active_history("group:12345") == ("合成回想甲", group_entries)
            private_entries = [(row[0], json.loads(row[2])) for row in before["mind_entries"]
                               if row[1] == "private:67890"]
            assert store.active_history("private:67890") == (None, private_entries)
        with sqlite3.connect(path) as db:
            assert db.execute(
                "SELECT compact_through,recap,last_message_seq FROM mind_sessions WHERE scene='group:12345'"
            ).fetchone() == (1, "合成回想甲", 4)
            assert db.execute(
                "SELECT compact_through,recap,last_message_seq FROM mind_sessions WHERE scene='private:67890'"
            ).fetchone() == (0, None, 3)
    elif format_number == 3:
        assert _version(tmp_path / "isolated.sqlite3.v4.bak") == (0x4C424E31, 4)
        assert _version(tmp_path / "isolated.sqlite3.v5.bak") == (0x4C424E31, 5)
        assert _version(tmp_path / "isolated.sqlite3.v6.bak") == (0x4C424E31, 6)
        assert _rows(path) == _rows(original_backup)
        with Store(path) as store:
            pending = store.pending_messages("group:12345")
            assert [(seq, message.platform_message_id, received_at)
                    for seq, message, received_at in pending] == [(5, "70005", 1789000005.5)]
            assert store.load_attention("group:12345") is None
            assert store.load_attention("private:67890") is None
            assert store.active_history("group:12345")[0] == "合成回想甲"
        with sqlite3.connect(path) as db:
            assert db.execute(
                "SELECT compact_through,recap,last_message_seq,attention_state "
                "FROM mind_sessions WHERE scene='group:12345'"
            ).fetchone() == (1, "合成回想甲", 4, None)
            assert db.execute(
                "SELECT compact_through,recap,last_message_seq,attention_state "
                "FROM mind_sessions WHERE scene='private:67890'"
            ).fetchone() == (0, None, 3, None)
            assert db.execute(
                "SELECT status,ended FROM turns WHERE id='synthetic-turn-queued'"
            ).fetchone() == ("queued", None)
    elif format_number == 4:
        assert _rows(path) == _rows(original_backup)
        with Store(path) as store:
            assert store.active_history("group:12345")[0] == "合成回想甲"
            assert store.load_attention("group:12345") == V6_ATTENTION
            assert store.pending_messages("group:12345")
        with sqlite3.connect(path) as db, sqlite3.connect(original_backup) as old:
            assert json.loads(old.execute(
                "SELECT attention_state FROM mind_sessions WHERE scene='group:12345'"
            ).fetchone()[0]) == V4_ATTENTION
            assert db.execute("SELECT scene,compact_through,recap,last_message_seq FROM mind_sessions ORDER BY scene").fetchall() == old.execute(
                "SELECT scene,compact_through,recap,last_message_seq FROM mind_sessions ORDER BY scene"
            ).fetchall()
            assert db.execute("SELECT count(*) FROM message_search").fetchone()[0] == len(before["messages"])
            assert db.execute("SELECT search_text FROM message_search WHERE rowid=6").fetchone()[0] == "甲乙\n丙丁"
            assert db.execute("SELECT search_text FROM message_search WHERE rowid=7").fetchone()[0] == 'alpha 中文检索 a"b'
            assert db.execute("SELECT search_text FROM message_search WHERE rowid=9").fetchone()[0] == "\n"
            assert [row[0] for row in db.execute(
                "SELECT rowid FROM message_search WHERE message_search MATCH ? ORDER BY rowid",
                ('"中文检"',),
            )] == [7, 8]
            assert db.execute(
                "SELECT count(*) FROM message_search WHERE message_search MATCH ?",
                ('"仅昵称不可"',),
            ).fetchone()[0] == 0
    elif format_number == 5:
        assert _rows(path) == _rows(original_backup)
        with Store(path) as store:
            assert store.active_history("group:12345")[0] == "合成回想甲"
            assert store.load_attention("group:12345") == V6_ATTENTION
            assert store.load_attention("private:67890") is None
            assert [(seq, message.platform_message_id) for seq, message, _ in
                    store.pending_messages("group:12345")] == [(5, "70005"), (6, "70006"),
                                                                  (7, "70007"), (9, "70009")]
        with sqlite3.connect(path) as db, sqlite3.connect(original_backup) as old:
            assert json.loads(old.execute(
                "SELECT attention_state FROM mind_sessions WHERE scene='group:12345'"
            ).fetchone()[0]) == V4_ATTENTION
            assert db.execute("SELECT scene,compact_through,recap,last_message_seq FROM mind_sessions ORDER BY scene").fetchall() == old.execute(
                "SELECT scene,compact_through,recap,last_message_seq FROM mind_sessions ORDER BY scene"
            ).fetchall()
            assert db.execute("SELECT seq,scene,platform_id,body,raw,received_at FROM messages ORDER BY seq").fetchall() == old.execute(
                "SELECT seq,scene,platform_id,body,raw,received_at FROM messages ORDER BY seq"
            ).fetchall()
            assert db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == old.execute(
                "SELECT rowid,search_text FROM message_search ORDER BY rowid"
            ).fetchall()
            assert db.execute("SELECT sql FROM sqlite_master WHERE name='message_search'").fetchone() == old.execute(
                "SELECT sql FROM sqlite_master WHERE name='message_search'"
            ).fetchone()
            assert [row[0] for row in db.execute(
                "SELECT rowid FROM message_search WHERE message_search MATCH ? ORDER BY rowid",
                ('"中文检"',),
            )] == [7, 8]
    else:
        assert _rows(path) == _rows(original_backup)
        with Store(path) as store:
            assert store.active_history("group:12345")[0] == "合成回想甲"
            assert store.load_attention("group:12345") == V6_ATTENTION
            assert store.load_attention("private:67890") is None
            assert store.pending_messages("group:12345")
            assert ScheduleStore(store).list_schedules("group:12345", status="all") == []
        with sqlite3.connect(path) as db, sqlite3.connect(original_backup) as old:
            assert db.execute(
                "SELECT scene,compact_through,recap,last_message_seq,attention_state "
                "FROM mind_sessions ORDER BY scene"
            ).fetchall() == old.execute(
                "SELECT scene,compact_through,recap,last_message_seq,attention_state "
                "FROM mind_sessions ORDER BY scene"
            ).fetchall()
            assert db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == old.execute(
                "SELECT rowid,search_text FROM message_search ORDER BY rowid"
            ).fetchall()
            assert {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='schedules'"
            )} == {"schedules_status_due", "schedules_legacy_identity"}


def test_v7_discovery_upgrade_preserves_actual_records_and_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v7-synthetic.sqlite3", path)
    assert _version(path) == (0x4C424E31, 7)
    with pytest.raises(ValueError, match="format 7 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v7.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 7)
    assert _version(tmp_path / "isolated.sqlite3.v8.bak") == (0x4C424E31, 8)
    assert _version(tmp_path / "isolated.sqlite3.v9.bak") == (0x4C424E31, 9)
    assert _version(tmp_path / "isolated.sqlite3.v10.bak") == (0x4C424E31, 10)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table in ("messages", "mind_entries", "turns", "schedules"):
            columns = (",".join(TURN_COLUMNS) if table == "turns" else
                       ",".join(SCHEDULE_COLUMNS) if table == "schedules" else
                       ",".join((*MESSAGE_COLUMNS, "received_at")) if table == "messages" else "*")
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == old.execute(
                f"SELECT {columns} FROM {table} ORDER BY rowid"
            ).fetchall()
        assert db.execute("SELECT interval_seconds FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute("SELECT cron FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute(f"SELECT {','.join(MODEL_CALL_COLUMNS)} FROM model_calls ORDER BY id").fetchall() == old.execute(
            f"SELECT {','.join(MODEL_CALL_COLUMNS)} FROM model_calls ORDER BY id"
        ).fetchall()
        assert db.execute(
            "SELECT scene,compact_through,recap,last_message_seq,attention_state "
            "FROM mind_sessions ORDER BY scene"
        ).fetchall() == old.execute(
            "SELECT scene,compact_through,recap,last_message_seq,attention_state "
            "FROM mind_sessions ORDER BY scene"
        ).fetchall()
        assert db.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall() == old.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        assert db.execute(
            "SELECT status,delivered_at,reason FROM schedules ORDER BY id"
        ).fetchall() == [
            ("pending", None, None),
            ("delivered", 1788993601.0, None),
            ("blocked", None, "PermissionError: 合成权限已撤销"),
            ("cancelled", None, None),
        ]
        assert db.execute(
            "SELECT discovered_tools FROM mind_sessions ORDER BY scene"
        ).fetchall() == [("[]",), ("[]",)]
        assert db.execute("SELECT COUNT(*) FROM web_documents").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM image_cache").fetchone()[0] == 0
    with Store(path) as store:
        assert store.load_discovered_tools("group:12345") == []
        assert store.load_discovered_tools("private:67890") == []
        assert store.load_discovered_tools("group:99999") == []


def test_v8_web_documents_upgrade_preserves_all_existing_records(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v8-synthetic.sqlite3", path)
    assert _version(path) == (0x4C424E31, 8)
    with pytest.raises(ValueError, match="format 8 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v8.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 8)
    assert _version(tmp_path / "isolated.sqlite3.v9.bak") == (0x4C424E31, 9)
    assert _version(tmp_path / "isolated.sqlite3.v10.bak") == (0x4C424E31, 10)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table in ("messages", "mind_entries", "turns", "mind_sessions", "schedules"):
            columns = (",".join(TURN_COLUMNS) if table == "turns" else
                       ",".join(SCHEDULE_COLUMNS) if table == "schedules" else
                       ",".join((*MESSAGE_COLUMNS, "received_at")) if table == "messages" else "*")
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == old.execute(
                f"SELECT {columns} FROM {table} ORDER BY rowid"
            ).fetchall()
        assert db.execute("SELECT interval_seconds FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute("SELECT cron FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute(f"SELECT {','.join(MODEL_CALL_COLUMNS)} FROM model_calls ORDER BY id").fetchall() == old.execute(
            f"SELECT {','.join(MODEL_CALL_COLUMNS)} FROM model_calls ORDER BY id"
        ).fetchall()
        assert db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == old.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        assert db.execute("SELECT discovered_tools FROM mind_sessions WHERE scene='group:12345'").fetchone()[0] == '["schedule_list"]'
        assert db.execute("SELECT sql FROM sqlite_master WHERE name='web_documents'").fetchone()[0] == (
            "CREATE TABLE web_documents (id INTEGER PRIMARY KEY, scene TEXT NOT NULL, body TEXT NOT NULL)"
        )
        assert db.execute("SELECT COUNT(*) FROM web_documents").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM image_cache").fetchone()[0] == 0
    with Store(path):
        pass


def test_v9_image_cache_upgrade_preserves_synthetic_web_and_chat_records(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v9-synthetic.sqlite3", path)
    assert _version(path) == (0x4C424E31, 9)
    with pytest.raises(ValueError, match="format 9 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v9.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 9)
    assert _version(tmp_path / "isolated.sqlite3.v10.bak") == (0x4C424E31, 10)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table in ("messages", "mind_entries", "turns", "mind_sessions", "schedules", "web_documents"):
            columns = (",".join(TURN_COLUMNS) if table == "turns" else
                       ",".join(SCHEDULE_COLUMNS) if table == "schedules" else
                       ",".join((*MESSAGE_COLUMNS, "received_at")) if table == "messages" else "*")
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == old.execute(
                f"SELECT {columns} FROM {table} ORDER BY rowid"
            ).fetchall()
        assert db.execute("SELECT interval_seconds FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute("SELECT cron FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute(f"SELECT {','.join(MODEL_CALL_COLUMNS)} FROM model_calls ORDER BY id").fetchall() == old.execute(
            f"SELECT {','.join(MODEL_CALL_COLUMNS)} FROM model_calls ORDER BY id"
        ).fetchall()
        assert db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == old.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        assert db.execute("SELECT COUNT(*) FROM web_documents").fetchone()[0] == 1
        webpage = json.loads(db.execute("SELECT body FROM web_documents WHERE id=1").fetchone()[0])
        assert webpage["url"] == "https://example.test/synthetic-source"
        assert webpage["content"].startswith("合成网页正文：这不是网络抓取记录。")
        assert db.execute("SELECT COUNT(*) FROM image_cache").fetchone()[0] == 0
        columns = [(row[1], row[2], row[5]) for row in db.execute("PRAGMA table_info(image_cache)")]
        assert columns == [
            ("scene", "TEXT", 1), ("platform_id", "TEXT", 2), ("image_index", "INTEGER", 3),
            ("jpeg", "BLOB", 0), ("width", "INTEGER", 0), ("height", "INTEGER", 0),
            ("animated", "INTEGER", 0), ("fetched_at", "REAL", 0),
            ("description", "TEXT", 0), ("description_model", "TEXT", 0),
            ("described_at", "REAL", 0),
        ]
    with Store(path):
        pass


def test_v10_call_position_upgrade_keeps_synthetic_native_groups_unpaired(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v10-synthetic.sqlite3", path)
    assert _version(path) == (0x4C424E31, 10)
    before = _rows(path)
    with sqlite3.connect(path) as old:
        other_before = {
            table: old.execute(
                f"SELECT {','.join(SCHEDULE_COLUMNS) if table == 'schedules' else '*'} "
                f"FROM {table} ORDER BY rowid"
            ).fetchall()
            for table in ("mind_sessions", "schedules", "web_documents", "image_cache")
        }
        search_before = old.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        old_columns = [row[1] for row in old.execute("PRAGMA table_info(model_calls)")]
        assert old_columns == list(MODEL_CALL_COLUMNS)
        assistants = [json.loads(row[0]) for row in old.execute(
            "SELECT message FROM mind_entries WHERE scene='group:12345' "
            "AND json_extract(message,'$.role')='assistant' ORDER BY seq"
        )]
        assert len(assistants) == 2
        assert [entry["tool_calls"][0]["id"] for entry in assistants] == [
            "synthetic-call-1", "synthetic-call-1",
        ]
    with pytest.raises(ValueError, match="format 10 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v10.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 10)
    assert _rows(path) == before == _rows(backup)
    assert _version(tmp_path / "isolated.sqlite3.v11.bak") == (0x4C424E31, 11)
    assert _version(tmp_path / "isolated.sqlite3.v12.bak") == (0x4C424E31, 12)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert [row[1] for row in db.execute("PRAGMA table_info(model_calls)")] == [
            *MODEL_CALL_COLUMNS, "mind_entry_seq", "cost", "scene", "plugin",
        ]
        assert [row[2] for row in db.execute("PRAGMA index_info(turn_calls)")] == [
            "turn_id", "id",
        ]
        assert old.execute("SELECT name FROM sqlite_master WHERE name='turn_calls'").fetchone() is None
        assert [row[1] for row in old.execute("PRAGMA table_info(model_calls)")] == old_columns
        assert db.execute("SELECT role,mind_entry_seq FROM model_calls ORDER BY id").fetchall() == [
            ("mind", None), ("mind", None), ("voice", None),
        ]
        assert db.execute("SELECT cost FROM model_calls ORDER BY id").fetchall() == [
            (None,), (None,), (None,),
        ]
        assert db.execute(
            f"SELECT {','.join(FIRST_EXPRESSION_COLUMNS)} FROM turns ORDER BY rowid"
        ).fetchall() == [(None, None, None), (None, None, None)]
        for table, rows in other_before.items():
            columns = ",".join(SCHEDULE_COLUMNS) if table == "schedules" else "*"
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == rows
            assert old.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == rows
        assert db.execute("SELECT interval_seconds FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute("SELECT cron FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == search_before
        assert old.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == search_before
        assert db.execute("SELECT length(jpeg),description FROM image_cache").fetchone()[1] == "合成图片描述"
    with Store(path):
        pass


def test_v11_first_expression_upgrade_preserves_synthetic_records_and_rowids(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v11-synthetic.sqlite3", path)
    before = _rows(path)
    with sqlite3.connect(path) as old:
        turns_before = old.execute(
            f"SELECT rowid,{','.join(TURN_COLUMNS)} FROM turns ORDER BY rowid"
        ).fetchall()
        calls_before = old.execute(
            "SELECT id,turn_id,role,mind_entry_seq FROM model_calls ORDER BY id"
        ).fetchall()
    assert calls_before == [
        (1, "synthetic-turn-1", "mind", 2),
        (2, "synthetic-turn-1", "mind", 5),
        (3, "synthetic-turn-1", "voice", None),
    ]
    assert _version(path) == (0x4C424E31, 11)
    with pytest.raises(ValueError, match="format 11 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v11.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 11)
    assert _rows(path) == before == _rows(backup)
    assert _version(tmp_path / "isolated.sqlite3.v12.bak") == (0x4C424E31, 12)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert [row[1] for row in db.execute("PRAGMA table_info(turns)")] == [
            *TURN_COLUMNS, *FIRST_EXPRESSION_COLUMNS,
        ]
        assert [row[1] for row in old.execute("PRAGMA table_info(turns)")] == list(TURN_COLUMNS)
        assert db.execute(
            f"SELECT rowid,{','.join(TURN_COLUMNS)} FROM turns ORDER BY rowid"
        ).fetchall() == turns_before
        assert db.execute(
            "SELECT id,turn_id,role,mind_entry_seq FROM model_calls ORDER BY id"
        ).fetchall() == calls_before
        assert db.execute("SELECT cost FROM model_calls ORDER BY id").fetchall() == [
            (None,), (None,), (None,),
        ]
        assert db.execute(
            f"SELECT {','.join(FIRST_EXPRESSION_COLUMNS)} FROM turns ORDER BY rowid"
        ).fetchall() == [(None, None, None)] * len(turns_before)
    with Store(path):
        pass


def test_v12_cost_upgrade_keeps_existing_latency_and_call_facts(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", path)
    before = _rows(path)
    with sqlite3.connect(path) as old:
        turns_before = old.execute("SELECT rowid,* FROM turns ORDER BY rowid").fetchall()
        calls_before = old.execute("SELECT rowid,* FROM model_calls ORDER BY id").fetchall()
        assert old.execute("SELECT first_expression_delivery FROM turns WHERE first_expression_at IS NOT NULL ORDER BY rowid").fetchall() == [
            ("sent",), ("simulated",),
        ]
        assert old.execute("SELECT mind_entry_seq FROM model_calls ORDER BY id").fetchall() == [
            (2,), (5,), (None,),
        ]
    assert _version(path) == (0x4C424E31, 12)
    with pytest.raises(ValueError, match="format 12 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v12.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 12)
    assert _rows(path) == before == _rows(backup)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert [row[1] for row in db.execute("PRAGMA table_info(model_calls)")] == [
            *MODEL_CALL_COLUMNS, "mind_entry_seq", "cost", "scene", "plugin",
        ]
        assert [row[1] for row in old.execute("PRAGMA table_info(model_calls)")] == [
            *MODEL_CALL_COLUMNS, "mind_entry_seq",
        ]
        assert db.execute("SELECT rowid,* FROM turns ORDER BY rowid").fetchall() == turns_before
        assert db.execute(
            f"SELECT rowid,{','.join(MODEL_CALL_COLUMNS)},mind_entry_seq FROM model_calls ORDER BY id"
        ).fetchall() == calls_before
        assert db.execute("SELECT cost FROM model_calls ORDER BY id").fetchall() == [
            (None,), (None,), (None,),
        ]
    with Store(path):
        pass


def test_v12_cost_column_conflict_rolls_back_without_changing_call_rows(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE model_calls ADD COLUMN cost TEXT")
        db.execute("UPDATE model_calls SET cost=? WHERE id=1", ('{"basis":"synthetic-preexisting"}',))
        columns_before = db.execute("PRAGMA table_info(model_calls)").fetchall()
        calls_before = db.execute("SELECT rowid,* FROM model_calls ORDER BY id").fetchall()

    with pytest.raises(sqlite3.OperationalError, match="duplicate column name: cost"):
        migrate_database(path)
    backup = tmp_path / "isolated.sqlite3.v12.bak"
    assert _version(path) == _version(backup) == (0x4C424E31, 12)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert db.execute("PRAGMA table_info(model_calls)").fetchall() == columns_before
        assert old.execute("PRAGMA table_info(model_calls)").fetchall() == columns_before
        assert db.execute("SELECT rowid,* FROM model_calls ORDER BY id").fetchall() == calls_before
        assert old.execute("SELECT rowid,* FROM model_calls ORDER BY id").fetchall() == calls_before


def test_v11_column_conflict_rolls_back_all_three_columns(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v11-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE turns ADD COLUMN first_expression_at REAL")
        db.execute("UPDATE turns SET first_expression_at=1789000007.5 WHERE id='synthetic-turn-1'")
        columns_before = db.execute("PRAGMA table_info(turns)").fetchall()
        turns_before = db.execute("SELECT rowid,* FROM turns ORDER BY rowid").fetchall()

    with pytest.raises(sqlite3.OperationalError, match="duplicate column name: first_expression_at"):
        migrate_database(path)
    backup = tmp_path / "isolated.sqlite3.v11.bak"
    assert _version(path) == _version(backup) == (0x4C424E31, 11)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert db.execute("PRAGMA table_info(turns)").fetchall() == columns_before
        assert old.execute("PRAGMA table_info(turns)").fetchall() == columns_before
        assert db.execute("SELECT rowid,* FROM turns ORDER BY rowid").fetchall() == turns_before
        assert old.execute("SELECT rowid,* FROM turns ORDER BY rowid").fetchall() == turns_before


def test_v10_migration_refuses_existing_backup_without_touching_records(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v10-synthetic.sqlite3", path)
    backup = tmp_path / "isolated.sqlite3.v10.bak"
    backup.write_bytes(b"existing format-10 backup must remain untouched")
    before = path.read_bytes()

    with pytest.raises(FileExistsError, match="Migration backup already exists"):
        migrate_database(path)
    assert path.read_bytes() == before
    assert backup.read_bytes() == b"existing format-10 backup must remain untouched"
    assert _version(path) == (0x4C424E31, 10)


def test_v10_index_conflict_rolls_back_new_call_column(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v10-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE INDEX turn_calls ON messages(scene)")
        before = db.execute("PRAGMA table_info(model_calls)").fetchall()

    with pytest.raises(sqlite3.OperationalError, match="turn_calls already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 10)
    backup = tmp_path / "isolated.sqlite3.v10.bak"
    assert _version(backup) == (0x4C424E31, 10)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert db.execute("PRAGMA table_info(model_calls)").fetchall() == before
        assert old.execute("PRAGMA table_info(model_calls)").fetchall() == before
        assert db.execute("SELECT sql FROM sqlite_master WHERE name='turn_calls'").fetchone() == (
            "CREATE INDEX turn_calls ON messages(scene)",
        )
        assert old.execute("SELECT sql FROM sqlite_master WHERE name='turn_calls'").fetchone() == (
            "CREATE INDEX turn_calls ON messages(scene)",
        )


def test_migration_refuses_existing_backup_before_any_step(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v1-synthetic.sqlite3", path)
    backup = path.with_name(path.name + ".v2.bak")
    backup.write_bytes(b"existing backup must remain untouched")
    before = _rows(path)
    with pytest.raises(FileExistsError):
        migrate_database(path)
    assert backup.read_bytes() == b"existing backup must remain untouched"
    assert not path.with_name(path.name + ".v1.bak").exists()
    assert _version(path) == (0x4C424E31, 1)
    assert _rows(path) == before


def test_work_task_upgrade_preserves_format_13_rows(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE model_calls ADD COLUMN cost TEXT")
        db.execute("UPDATE model_calls SET cost=?", ('{"currency":"CNY","amount":"0.123"}',))
        db.execute("PRAGMA user_version=13")
        calls_before = db.execute("SELECT rowid,* FROM model_calls ORDER BY rowid").fetchall()
    before = _rows(path)
    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v13.bak"
    assert _version(backup) == (0x4C424E31, 13)
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _rows(path) == before == _rows(backup)
    with Store(path) as store:
        assert [tuple(row) for row in store.db.execute(
            f"SELECT rowid,{_historical_columns('model_calls', store.db)} FROM model_calls ORDER BY rowid")] == calls_before
        for table in ("tasks", "task_events", "task_files"):
            assert store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_v14_interval_upgrade_preserves_one_time_schedules_chat_and_tasks(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", seed)
    migrate_database(seed)
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(tmp_path / "seed.sqlite3.v14.bak", path)
    assert _version(path) == (0x4C424E31, 14)

    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO tasks(id,scene,requester,goal,deliverable,context,input,status,"
            "created,started,ended,container,question,summary,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (51, "group:12345", "10001", "合成资料整理", "合成摘要", "合成上下文", "合成原始任务要求",
             "done", 1789000100.0, 1789000101.0, 1789000109.0, None, None, "已完成合成摘要", None),
        )
        db.execute(
            "INSERT INTO task_events(id,scene,task_id,kind,body,notice,created,delivered_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (71, "group:12345", 51, "finished", json.dumps({"status": "done", "summary": "已完成合成摘要"}),
             "合成任务完成通知", 1789000109.0, None),
        )
        db.execute(
            "INSERT INTO task_files(id,scene,task_id,name,path,size,note,created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (81, "group:12345", 51, "summary.txt", "out/summary.txt", 18, "合成交付副本", 1789000108.0),
        )
        schedules_before = db.execute(
            f"SELECT {','.join(SCHEDULE_COLUMNS)} FROM schedules ORDER BY id"
        ).fetchall()
        task_rows_before = {table: db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall()
                            for table in ("tasks", "task_events", "task_files")}
    assert [row[8] for row in schedules_before] == ["pending", "delivered", "blocked", "cancelled"]
    chat_before = _rows(path)
    with pytest.raises(ValueError, match="format 14 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v14.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 14)
    assert _rows(path) == chat_before == _rows(backup)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert [row[1] for row in db.execute("PRAGMA table_info(schedules)")] == [
            *SCHEDULE_COLUMNS, "interval_seconds", "cron", "legacy_source",
        ]
        assert [row[1] for row in old.execute("PRAGMA table_info(schedules)")] == list(SCHEDULE_COLUMNS)
        for source in (db, old):
            assert source.execute(
                f"SELECT {','.join(SCHEDULE_COLUMNS)} FROM schedules ORDER BY id"
            ).fetchall() == schedules_before
            for table, rows in task_rows_before.items():
                assert source.execute(f"SELECT rowid,{_historical_columns(table, source)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert db.execute("SELECT interval_seconds FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        assert db.execute("SELECT cron FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
    with Store(path) as store:
        assert [schedule.status for schedule in ScheduleStore(store).list_schedules("group:12345", status="all")] == [
            "delivered", "blocked", "pending",
        ]
        assert TaskStore(store).get("group:12345", 51).input == "合成原始任务要求"
        assert TaskStore(store).get_file("group:12345", 51, 81).name == "summary.txt"


def test_v15_daily_cron_upgrade_preserves_interval_schedules_chat_and_tasks(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", seed)
    migrate_database(seed)
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(tmp_path / "seed.sqlite3.v15.bak", path)
    assert _version(path) == (0x4C424E31, 15)

    with sqlite3.connect(path) as db:
        db.execute("UPDATE schedules SET interval_seconds=3600 WHERE id=1")
        db.execute(
            "INSERT INTO tasks(id,scene,requester,goal,deliverable,context,input,status,"
            "created,started,ended,container,question,summary,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (52, "group:12345", "10002", "合成定时资料任务", "合成文档", "合成背景", "补充后的真实要求",
             "waiting_input", 1789000200.0, 1789000201.0, None, None,
             json.dumps({"question": "还需要哪一页？"}), None, None),
        )
        db.execute(
            "INSERT INTO task_events(id,scene,task_id,kind,body,notice,created,delivered_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (72, "group:12345", 52, "question", json.dumps({"question": "还需要哪一页？"}),
             None, 1789000202.0, None),
        )
        db.execute(
            "INSERT INTO task_files(id,scene,task_id,name,path,size,note,created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (82, "group:12345", 52, "draft.txt", "out/draft.txt", 12, "合成草稿副本", 1789000203.0),
        )
        schedules_before = db.execute(
            f"SELECT {','.join(SCHEDULE_V15_COLUMNS)} FROM schedules ORDER BY id"
        ).fetchall()
        task_rows_before = {table: db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall()
                            for table in ("tasks", "task_events", "task_files")}
    assert [(row[8], row[-1]) for row in schedules_before] == [
        ("pending", 3600), ("delivered", None), ("blocked", None), ("cancelled", None),
    ]
    chat_before = _rows(path)
    with pytest.raises(ValueError, match="format 15 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v15.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 15)
    assert _rows(path) == chat_before == _rows(backup)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert [row[1] for row in db.execute("PRAGMA table_info(schedules)")] == [
            *SCHEDULE_V15_COLUMNS, "cron", "legacy_source",
        ]
        assert [row[1] for row in old.execute("PRAGMA table_info(schedules)")] == list(SCHEDULE_V15_COLUMNS)
        for source in (db, old):
            assert source.execute(
                f"SELECT {','.join(SCHEDULE_V15_COLUMNS)} FROM schedules ORDER BY id"
            ).fetchall() == schedules_before
            for table, rows in task_rows_before.items():
                assert source.execute(f"SELECT rowid,{_historical_columns(table, source)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert db.execute("SELECT cron FROM schedules ORDER BY id").fetchall() == [(None,)] * 4
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE schedules SET cron='cron:0 12 * * *' WHERE id=1")
    with Store(path) as store:
        assert ScheduleStore(store).get_schedule("group:12345", 1).interval_seconds == 3600
        assert ScheduleStore(store).get_schedule("group:12345", 1).cron is None
        assert TaskStore(store).get("group:12345", 52).question == {"question": "还需要哪一页？"}
        assert TaskStore(store).get_file("group:12345", 52, 82).name == "draft.txt"


def test_v16_media_upgrade_preserves_existing_chat_identity_tasks_and_schedules(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", seed)
    migrate_database(seed)
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(tmp_path / "seed.sqlite3.v16.bak", path)
    assert _version(path) == (0x4C424E31, 16)

    with sqlite3.connect(path) as db:
        db.execute("UPDATE schedules SET interval_seconds=3600 WHERE id=1")
        db.execute("UPDATE schedules SET cron_minute_of_day=0 WHERE id=2")
        db.execute(
            "INSERT INTO tasks(id,scene,requester,goal,deliverable,context,input,status,"
            "created,started,ended,container,question,summary,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (55, "group:12345", "10003", "合成媒体整理", "合成图片说明", "合成上下文",
             "保留实际任务输入", "waiting_input", 1789000300.0, 1789000301.0,
             None, None, json.dumps({"question": "合成询问"}), None, None),
        )
        db.execute(
            "INSERT INTO task_events(id,scene,task_id,kind,body,notice,created,delivered_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (75, "group:12345", 55, "question", json.dumps({"question": "合成询问"}),
             "合成任务待答通知", 1789000302.0, None),
        )
        db.execute(
            "INSERT INTO task_files(id,scene,task_id,name,path,size,note,created) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (85, "group:12345", 55, "draft.txt", "out/draft.txt", 19,
             "合成任务文件", 1789000303.0),
        )

    preserved = ("messages", "mind_entries", "mind_sessions", "turns", "model_calls",
                 "schedules", "tasks", "task_events", "task_files", "web_documents",
                 "image_cache")
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall()
                  for table in preserved}
        search_before = db.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        assert before["messages"] and before["tasks"] and before["schedules"]
    with pytest.raises(ValueError, match="format 16 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v16.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 16)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            columns = "rowid," + ",".join(OLD_MEDIA_COLUMNS) if table == "media" else f"rowid,{_historical_columns(table, db)}"
            expected = _daily_cron_rows(rows) if table == "schedules" else rows
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == expected
            assert old.execute(f"SELECT rowid,{_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        for source in (db, old):
            assert source.execute(
                "SELECT rowid,search_text FROM message_search ORDER BY rowid"
            ).fetchall() == search_before
        assert [row[1] for row in db.execute("PRAGMA table_info(media)")] == [
            "id", "persona_id", "file", "source_message_seq", "source_image_index",
            "mime_type", "width", "height", "animated", "data",
        ]
        assert [row[1] for row in db.execute("PRAGMA table_info(message_media)")] == [
            "message_seq", "image_index", "media_id", "description", "emotions", "tags",
        ]
        assert [row[2] for row in db.execute("PRAGMA index_info(media_persona_file)")] == [
            "persona_id", "file", "id",
        ]
        assert db.execute("SELECT COUNT(*) FROM media").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM message_media").fetchone() == (0,)
        assert old.execute("SELECT name FROM sqlite_master WHERE name='media'").fetchone() is None
        assert old.execute("SELECT name FROM sqlite_master WHERE name='message_media'").fetchone() is None
    with Store(path):
        pass


def test_v17_learning_upgrade_preserves_original_media_chat_and_task_records(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", seed)
    migrate_database(seed)
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(tmp_path / "seed.sqlite3.v17.bak", path)
    assert _version(path) == (0x4C424E31, 17)

    image = BytesIO()
    Image.new("RGB", (2, 1), "blue").save(image, format="PNG")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE schedules SET interval_seconds=3600 WHERE id=1")
        db.execute("UPDATE schedules SET cron_minute_of_day=0 WHERE id=2")
        db.execute(
            "INSERT INTO media(id,persona_id,file,mime_type,width,height,animated,data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (61, "synthetic-role", "blue.png", "image/png", 2, 1, 0, image.getvalue()),
        )
        db.execute(
            "INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) "
            "VALUES (?,?,?,?,?,?)",
            (1, 1, 61, "合成蓝色图片", '["平静"]', '["合成"]'),
        )
        db.execute(
            "INSERT INTO tasks(id,scene,requester,goal,deliverable,context,input,status,"
            "created,started,ended,container,question,summary,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (56, "group:12345", "10004", "合成资料收集", "合成说明", "合成任务背景",
             "保留任务输入原文", "done", 1789000400.0, 1789000401.0,
             1789000410.0, None, None, "合成完成内容", None),
        )
        db.execute(
            "INSERT INTO task_events(id,scene,task_id,kind,body,notice,created,delivered_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (76, "group:12345", 56, "finished", '{"status":"done"}',
             "合成完成通知", 1789000410.0, None),
        )

    preserved = ("messages", "mind_entries", "mind_sessions", "turns", "model_calls",
                 "schedules", "tasks", "task_events", "task_files", "web_documents",
                 "image_cache", "media", "message_media")
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall()
                  for table in preserved}
        search_before = db.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        assert any(row[5] is not None for row in before["messages"])
        assert before["media"] and before["message_media"] and before["tasks"] and before["schedules"]
    with pytest.raises(ValueError, match="format 17 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v17.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 17)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            columns = "rowid," + ",".join(OLD_MEDIA_COLUMNS) if table == "media" else f"rowid,{_historical_columns(table, db)}"
            expected = _daily_cron_rows(rows) if table == "schedules" else rows
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == expected
            assert old.execute(f"SELECT rowid,{_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert all(row == (None, None) for row in db.execute(
            "SELECT source_message_seq,source_image_index FROM media ORDER BY id"
        ))
        for source in (db, old):
            assert source.execute(
                "SELECT rowid,search_text FROM message_search ORDER BY rowid"
            ).fetchall() == search_before
        for table in ("learning_state", "learning_batches", "expressions"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
            assert old.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
        assert [row[2] for row in db.execute("PRAGMA index_info(learning_batches_scene)")] == [
            "scene", "id",
        ]
        assert [row[2] for row in db.execute("PRAGMA index_info(expressions_scene_status)")] == [
            "scene", "status", "id",
        ]
    with Store(path):
        pass


def test_v18_expression_vector_upgrade_keeps_learning_and_other_synthetic_records(tmp_path: Path) -> None:
    seed = tmp_path / "seed.sqlite3"
    shutil.copyfile(FIXTURES / "v12-synthetic.sqlite3", seed)
    migrate_database(seed)
    source = tmp_path / "seed.sqlite3.v18.bak"
    assert _version(source) == (0x4C424E31, 18)
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(source, path)

    image = BytesIO()
    Image.new("RGB", (2, 1), "blue").save(image, format="PNG")
    cached_image = BytesIO()
    Image.new("RGB", (2, 1), "blue").save(cached_image, format="JPEG")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE schedules SET interval_seconds=3600 WHERE id=1")
        db.execute("UPDATE schedules SET cron_minute_of_day=0 WHERE id=2")
        db.execute(
            "INSERT INTO media(id,persona_id,file,mime_type,width,height,animated,data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (61, "synthetic-role", "blue.png", "image/png", 2, 1, 0, image.getvalue()),
        )
        db.execute(
            "INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) "
            "VALUES (?,?,?,?,?,?)",
            (1, 1, 61, "合成蓝色图片", '["平静"]', '["合成"]'),
        )
        db.execute(
            "INSERT INTO tasks(id,scene,requester,goal,deliverable,context,input,status,"
            "created,started,ended,container,question,summary,error) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (56, "group:12345", "10004", "合成资料收集", "合成说明", "合成任务背景",
             "合成任务输入", "done", 1789000400.0, 1789000401.0, 1789000410.0,
             None, None, "合成完成内容", None),
        )
        db.execute(
            "INSERT INTO task_events(id,scene,task_id,kind,body,notice,created,delivered_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (76, "group:12345", 56, "finished", '{"status":"done"}',
             "合成完成通知", 1789000410.0, None),
        )
        db.execute(
            "INSERT INTO task_files(id,scene,task_id,name,path,size,note,created) VALUES (?,?,?,?,?,?,?,?)",
            (86, "group:12345", 56, "synthetic.txt", "data/tasks/synthetic.txt", 12,
             "合成交付说明", 1789000411.0),
        )
        db.execute("INSERT INTO web_documents(id,scene,body) VALUES (?,?,?)",
                   (96, "group:12345", json.dumps({
                       "url": "https://example.invalid/synthetic",
                       "final_url": "https://example.invalid/synthetic",
                       "fetched_at": 1789000450.0, "media_type": "text/plain",
                       "content": "合成网页正文", "notice": "合成内容",
                   }, ensure_ascii=False)))
        db.execute(
            "INSERT INTO image_cache(scene,platform_id,image_index,jpeg,width,height,animated,"
            "fetched_at,description,description_model,described_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("group:12345", "70001", 1, cached_image.getvalue(), 2, 1, 0,
             1789000500.0, "合成图描述", "synthetic-vision", 1789000501.0),
        )
        db.executemany("INSERT INTO learning_state(scene,after_seq) VALUES (?,?)",
                       [("group:12345", 9), ("private:67890", 3)])
        db.executemany(
            "INSERT INTO learning_batches(id,scene,after_seq,through_seq,started,ended,model_started,"
            "status,request,response,usage,cost,error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (71, "group:12345", 0, 5, 1789000600.0, 1789000602.0, 1789000601.0,
                 "complete", '{"messages":["合成输入甲"]}', '{"candidates":["合成候选"]}',
                 '{"input_tokens":12,"output_tokens":4}', '{"currency":"CNY","amount":"0.01"}', None),
                (72, "group:12345", 5, 9, 1789000610.0, 1789000612.0, 1789000611.0,
                 "failed", '{"messages":["合成输入乙"]}', None,
                 '{"input_tokens":3}', None, "合成协议错误原文"),
                (73, "private:67890", 0, 3, 1789000620.0, 1789000621.0, None,
                 "interrupted", '{"messages":["合成输入丙"]}', None, None, None,
                 "合成中断原文"),
            ],
        )
        db.executemany(
            "INSERT INTO expressions(id,scene,situation,style,sources,status,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                (81, "group:12345", "合成情境甲", "合成说法甲", "[1,2]", "pending", 1789000700.0),
                (82, "group:12345", "合成情境乙", "合成说法乙", "[4,5]", "adopted", 1789000701.0),
                (83, "private:67890", "合成情境丙", "合成说法丙", "[3]", "rejected", 1789000702.0),
            ],
        )

    preserved = ("messages", "mind_entries", "mind_sessions", "turns", "model_calls",
                 "schedules", "tasks", "task_events", "task_files", "web_documents",
                 "image_cache", "media", "message_media", "learning_state",
                 "learning_batches", "expressions")
    with sqlite3.connect(path) as old:
        before = {table: old.execute(f"SELECT rowid,{_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall()
                  for table in preserved}
        search_before = old.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        assert [row[6] for row in before["expressions"]] == ["pending", "adopted", "rejected"]
        assert [row[8] for row in before["learning_batches"]] == ["complete", "failed", "interrupted"]
        assert before["messages"] and before["media"] and before["task_files"] and before["schedules"]
    with pytest.raises(ValueError, match="format 18 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v18.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 18)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            if table == "expressions":
                assert db.execute(
                    "SELECT rowid,id,scene,situation,style,sources,status,updated "
                    "FROM expressions ORDER BY rowid"
                ).fetchall() == rows
            elif table == "media":
                assert db.execute(
                    "SELECT rowid," + ",".join(OLD_MEDIA_COLUMNS) + " FROM media ORDER BY rowid"
                ).fetchall() == rows
            elif table == "schedules":
                assert db.execute(f"SELECT rowid,{_historical_columns('schedules', db)} FROM schedules ORDER BY rowid").fetchall() == _daily_cron_rows(rows)
            else:
                assert db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() == rows
            assert old.execute(f"SELECT rowid,{_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert all(row == (None, None) for row in db.execute(
            "SELECT source_message_seq,source_image_index FROM media ORDER BY id"
        ))
        for source_db in (db, old):
            assert source_db.execute(
                "SELECT rowid,search_text FROM message_search ORDER BY rowid"
            ).fetchall() == search_before
        assert [row[1] for row in db.execute("PRAGMA table_info(expressions)")][-3:] == [
            "vector", "vector_binding", "vector_dimensions",
        ]
        assert not {"vector", "vector_binding", "vector_dimensions"}.intersection(
            row[1] for row in old.execute("PRAGMA table_info(expressions)")
        )
        assert all(row == (None, None, None) for row in db.execute(
            "SELECT vector,vector_binding,vector_dimensions FROM expressions ORDER BY id"
        ))
        assert old.execute("SELECT name FROM sqlite_master WHERE name='expression_embedding_calls'").fetchone() is None
        assert db.execute("SELECT COUNT(*) FROM expression_embedding_calls").fetchone() == (0,)
        assert [row[2] for row in db.execute("PRAGMA index_info(expression_embedding_scene)")] == [
            "scene", "id",
        ]
    with Store(path):
        pass


def test_migration_rejects_current_and_wrong_database(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v2-synthetic.sqlite3", path)
    migrate_database(path)
    with pytest.raises(ValueError, match=f"Expected a next-core format 1 through {FORMAT_VERSION - 1} database"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)

    unrelated = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(unrelated) as db:
        db.execute("CREATE TABLE other (value TEXT)")
        db.execute("INSERT INTO other VALUES ('untouched')")
    with pytest.raises(ValueError, match=f"Expected a next-core format 1 through {FORMAT_VERSION - 1} database"):
        migrate_database(unrelated)
    assert not unrelated.with_name(unrelated.name + ".v1.bak").exists()
    with sqlite3.connect(unrelated) as db:
        assert db.execute("SELECT value FROM other").fetchone()[0] == "untouched"


def test_format19_jargon_migration_preserves_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v19-synthetic.sqlite3", path)
    old_tables = (
        "messages", "mind_entries", "mind_sessions", "turns", "model_calls", "schedules",
        "tasks", "task_events", "task_files", "web_documents", "image_cache", "media",
        "message_media", "learning_state", "learning_batches", "expressions",
        "expression_embedding_calls",
    )
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall()
                  for table in old_tables}
        search_before = db.execute(
            "SELECT rowid,search_text FROM message_search ORDER BY rowid"
        ).fetchall()
        assert before["messages"] and before["model_calls"] and before["tasks"]
        assert before["schedules"] and before["expressions"] and before["expression_embedding_calls"]
    with pytest.raises(ValueError, match="format 19 requires offline migration"):
        Store(path)
    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v19.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 19)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            assert db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() == rows
            assert old.execute(f"SELECT {_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        for connection in (db, old):
            assert connection.execute(
                "SELECT rowid,search_text FROM message_search ORDER BY rowid"
            ).fetchall() == search_before
        assert all(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
                   for table in ("jargon_state", "jargon", "jargon_calls"))
        assert all(old.execute(
            "SELECT name FROM sqlite_master WHERE name=?", (table,)
        ).fetchone() is None for table in ("jargon_state", "jargon", "jargon_calls"))
        assert [row[2] for row in db.execute("PRAGMA index_info(jargon_scene_status)")] == [
            "scene", "status", "id",
        ]
        assert [row[2] for row in db.execute("PRAGMA index_info(jargon_calls_scene)")] == [
            "scene", "id",
        ]
    with Store(path):
        pass


def test_format19_jargon_migration_collision_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v19-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE jargon (collision TEXT)")
    with sqlite3.connect(path) as db:
        before = db.execute("SELECT * FROM expressions ORDER BY id").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 19)
    assert _version(path.with_name(path.name + ".v19.bak")) == (0x4C424E31, 19)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM expressions ORDER BY id").fetchall() == before
        assert db.execute("SELECT name FROM sqlite_master WHERE name='jargon_state'").fetchone() is None
        assert db.execute("SELECT name FROM sqlite_master WHERE name='jargon_calls'").fetchone() is None


def _remove_format32_to35_additions(db):
    db.execute('ALTER TABLE model_calls DROP COLUMN scene')
    db.execute('ALTER TABLE model_calls DROP COLUMN plugin')
    db.execute('DROP INDEX schedules_legacy_identity')
    db.execute('ALTER TABLE schedules DROP COLUMN legacy_source')
    db.execute('ALTER TABLE messages DROP COLUMN persona_id')
    db.execute('ALTER TABLE tasks DROP COLUMN materials')


def _remove_format31_indexes(db):
    _remove_format32_to35_additions(db)
    if "cost" in [row[1] for row in db.execute("PRAGMA table_info(audio_calls)")]:
        db.execute("ALTER TABLE audio_calls DROP COLUMN cost")
    # Construct an earlier-format input from the current schema, not a partial current database.
    for name in ('message_send_window','message_retention','model_call_usage','model_call_expiry',
                 'turns_scene_time','turns_expiry','task_model_usage','audio_calls_usage','learning_batches_usage',
                 'jargon_calls_usage','sticker_calls_usage','reply_effect_calls_usage','expression_embedding_calls_usage'):
        db.execute(f'DROP INDEX IF EXISTS {name}')


def _format20_source(path: Path) -> None:
    """A format-19 synthetic fixture plus the committed format-20 jargon DDL."""
    shutil.copyfile(FIXTURES / "v19-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE jargon_state (
                scene TEXT PRIMARY KEY, start_seq INTEGER NOT NULL, after_seq INTEGER NOT NULL
            );
            CREATE TABLE jargon (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, term TEXT NOT NULL,
                count INTEGER NOT NULL, sample_seqs TEXT NOT NULL,
                latest_meaning TEXT, confidence REAL, meaning TEXT,
                last_inference_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK(status IN ('pending','adopted','rejected')),
                updated REAL NOT NULL, UNIQUE(scene,term)
            );
            CREATE INDEX jargon_scene_status ON jargon(scene,status,id);
            CREATE TABLE jargon_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
                purpose TEXT NOT NULL CHECK(purpose IN ('discovery','meaning')),
                after_seq INTEGER, through_seq INTEGER, term_id INTEGER, inference_count INTEGER,
                started REAL NOT NULL, ended REAL,
                status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),
                model_started REAL, request TEXT NOT NULL,
                response TEXT, usage TEXT, cost TEXT, error TEXT
            );
            CREATE INDEX jargon_calls_scene ON jargon_calls(scene,id);
            PRAGMA user_version = 20;
        """)
        db.execute("INSERT INTO jargon_state VALUES (?,?,?)", ("group:80001", 1, 1))
        db.execute(
            "INSERT INTO jargon(scene,term,count,sample_seqs,latest_meaning,confidence,meaning,"
            "last_inference_count,status,updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("group:80001", "合成词", 4, "[1]", "合成推断", 0.75,
             "人工解释", 4, "adopted", 1790000000.0),
        )
        db.execute(
            "INSERT INTO jargon_calls(scene,purpose,term_id,inference_count,started,ended,"
            "status,model_started,request,response,usage,cost) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("group:80001", "meaning", 1, 4, 1790000000.0, 1790000001.0, "complete",
             1790000000.1, '{"synthetic":true}', '{"meaning":"合成推断","confidence":0.75}',
             '{"prompt_tokens":10}', '{"basis":"configured_estimate","currency":"CNY","amount":"0.01"}'),
        )
        db.execute(
            "INSERT INTO expressions(id,scene,situation,style,sources,status,updated,vector,"
            "vector_binding,vector_dimensions) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (100, "group:80001", "曾用高编号", "合成说法", "[1]", "adopted",
             1790000000.0, b"\x00\x00\x80?", '{"provider":"synthetic"}', 1),
        )
        db.execute(
            "INSERT INTO model_calls(turn_id,role,started,ended,request,response,usage) "
            "VALUES (?,?,?,?,?,?,?)",
            ("synthetic-turn", "voice", 1790000001.0, 1790000002.0,
             '{"settings":{"model":"synthetic"},"expression_ids":[100]}',
             '{"message":{"role":"assistant","content":"合成输出"}}',
             '{"prompt_tokens":1,"completion_tokens":1}'),
        )
        db.execute("DELETE FROM expressions WHERE id=100")


def test_format20_expression_ids_do_not_reuse_deleted_voice_references(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format20_source(path)
    tables = ("messages", "mind_entries", "mind_sessions", "turns", "model_calls",
              "schedules", "tasks", "task_events", "task_files", "learning_state",
              "learning_batches", "expressions", "expression_embedding_calls",
              "jargon_state", "jargon", "jargon_calls")
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall()
                  for table in tables}
        fts_before = db.execute("SELECT rowid,search_text FROM message_search").fetchall()
        assert before["expressions"] and before["jargon"] and before["jargon_calls"]
        assert 100 not in [row[0] for row in before["expressions"]]
    with pytest.raises(ValueError, match="format 20 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v20.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 20)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            assert db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() == rows
            assert old.execute(f"SELECT {_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert db.execute("SELECT rowid,search_text FROM message_search").fetchall() == fts_before
        assert old.execute("SELECT rowid,search_text FROM message_search").fetchall() == fts_before
        assert db.execute("SELECT seq FROM sqlite_sequence WHERE name='expressions'").fetchone() == (100,)
        assert [row[2] for row in db.execute("PRAGMA index_info(expressions_scene_status)")] == [
            "scene", "status", "id",
        ]

    with Store(path) as store:
        records = LearningStore(store)

        def add(number: int, content: str) -> int:
            raw = {"post_type": "message", "message_type": "group", "self_id": "90001",
                   "group_id": "80001", "user_id": "70001", "message_id": f"synthetic-new-{number}",
                   "time": 1790000000 + number, "sender": {"nickname": "合成甲", "role": "member"},
                   "message": [{"type": "text", "data": {"text": content}}]}
            return store.enqueue(parse_message(raw, own_message_ids=set()), raw, 1790000000.0 + number)

        prior = records.state("group:80001")["after_seq"]
        first_seq = add(2, "新合成情境甲")
        first_batch = records.begin("group:80001", prior, first_seq, {"synthetic": True})
        records.complete(first_batch, [("新情境甲", "新说法甲", [first_seq])], auto_adopt=False)
        first = records.find_expression("group:80001", "新情境甲", "新说法甲")
        assert first["id"] > 100
        assert records.delete_expression("group:80001", first["id"])
        second_seq = add(3, "新合成情境乙")
        second_batch = records.begin("group:80001", first_seq, second_seq, {"synthetic": True})
        records.complete(second_batch, [("新情境乙", "新说法乙", [second_seq])], auto_adopt=False)
        second = records.find_expression("group:80001", "新情境乙", "新说法乙")
        assert second["id"] > first["id"]
        assert records.recent_expression_ids("group:80001") == {100}


def test_format20_expression_rebuild_failure_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format20_source(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE expressions_next (collision TEXT)")
        before = db.execute("SELECT * FROM expressions ORDER BY id").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 20)
    assert _version(path.with_name(path.name + ".v20.bak")) == (0x4C424E31, 20)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM expressions ORDER BY id").fetchall() == before
        assert db.execute("SELECT name FROM sqlite_master WHERE name='expressions_scene_status'").fetchone()


def _format21_source(path: Path) -> None:
    """Keep the committed format-21 media shape as the offline source."""
    _format20_source(path)
    with sqlite3.connect(path) as db:
        db.executescript("""
            BEGIN;
            CREATE TABLE expressions_next (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,
                situation TEXT NOT NULL, style TEXT NOT NULL, sources TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending','adopted','rejected')),
                updated REAL NOT NULL, vector BLOB, vector_binding TEXT,
                vector_dimensions INTEGER, UNIQUE(scene,situation,style)
            );
            INSERT INTO expressions_next(id,scene,situation,style,sources,status,updated,
                vector,vector_binding,vector_dimensions)
                SELECT id,scene,situation,style,sources,status,updated,
                    vector,vector_binding,vector_dimensions FROM expressions;
            DROP TABLE expressions;
            ALTER TABLE expressions_next RENAME TO expressions;
            CREATE INDEX expressions_scene_status ON expressions(scene,status,id);
            INSERT INTO expressions(id,scene,situation,style,sources,status,updated)
                VALUES (100,'group:80001','已删历史编号','合成说法','[1]','pending',1790000000.0);
            DELETE FROM expressions WHERE id=100;
            PRAGMA user_version = 21;
            COMMIT;
        """)
        raw = {"post_type": "message", "message_type": "group", "self_id": "90001",
               "group_id": "80001", "user_id": "70001", "message_id": "synthetic-image-source",
               "time": 1790000002, "sender": {"nickname": "合成甲", "role": "member"},
               "message": [{"type": "image", "data": {"url": "https://example.com/synthetic.png"}}]}
        message = parse_message(raw, own_message_ids=set())
        db.execute(
            "INSERT INTO messages(seq,scene,platform_id,body,raw,received_at) VALUES (?,?,?,?,?,?)",
            (2, message.scene, message.platform_message_id,
             json.dumps(asdict(message), ensure_ascii=False), json.dumps(raw, ensure_ascii=False),
             1790000002.0),
        )
        db.execute("INSERT INTO message_search(rowid,search_text) VALUES (?,?)",
                   (2, plain_text(message).casefold()))
        image = BytesIO()
        Image.new("RGB", (2, 2), "blue").save(image, format="PNG")
        db.execute(
            "INSERT INTO media(id,persona_id,file,mime_type,width,height,animated,data) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (90, "role-fixture", "stickers/synthetic.png", "image/png", 2, 2, 0, image.getvalue()),
        )
        db.execute(
            "INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) "
            "VALUES (?,?,?,?,?,?)",
            (2, 1, 90, "合成角色表情", '["开心"]', '["蓝色"]'),
        )


def test_format21_media_migration_preserves_originals_and_other_rows(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format21_source(path)
    old_tables = (
        "messages", "mind_entries", "mind_sessions", "turns", "model_calls", "schedules",
        "tasks", "task_events", "task_files", "web_documents", "image_cache", "message_media",
        "learning_state", "learning_batches", "expressions", "expression_embedding_calls",
        "jargon_state", "jargon", "jargon_calls",
    )
    with sqlite3.connect(path) as db:
        before = {table: db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall()
                  for table in old_tables}
        old_media = db.execute(
            "SELECT id,persona_id,file,mime_type,width,height,animated,data FROM media ORDER BY id"
        ).fetchall()
        search_before = db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall()
        assert before["jargon"] and before["expressions"] and before["model_calls"]
        assert old_media and before["message_media"]
    with pytest.raises(ValueError, match="format 21 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v21.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 21)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            assert db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() == rows
            assert old.execute(f"SELECT {_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        for connection in (db, old):
            assert connection.execute(
                "SELECT rowid,search_text FROM message_search ORDER BY rowid"
            ).fetchall() == search_before
            assert connection.execute(
                "SELECT id,persona_id,file,mime_type,width,height,animated,data FROM media ORDER BY id"
            ).fetchall() == old_media
        assert db.execute(
            "SELECT source_message_seq,source_image_index FROM media WHERE id=90"
        ).fetchone() == (None, None)
        assert {"source_message_seq", "source_image_index"}.issubset(
            row[1] for row in db.execute("PRAGMA table_info(media)")
        )
        assert not {"source_message_seq", "source_image_index"}.intersection(
            row[1] for row in old.execute("PRAGMA table_info(media)")
        )
        assert all(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
                   for table in ("sticker_candidates", "sticker_calls"))
        assert [row[2] for row in db.execute("PRAGMA index_info(message_media_media)")] == [
            "media_id", "message_seq",
        ]
    with Store(path) as store:
        assert store.original_image("group:80001", 2, 1) == ("image/png", old_media[0][-1])
        assert store.message_media("group:80001", 2)[0]["file"] == "stickers/synthetic.png"


def test_format21_media_rebuild_collision_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format21_source(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE sticker_candidates (collision TEXT)")
        before_media = db.execute("SELECT * FROM media ORDER BY id").fetchall()
        before_links = db.execute("SELECT * FROM message_media ORDER BY message_seq,image_index").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 21)
    assert _version(path.with_name(path.name + ".v21.bak")) == (0x4C424E31, 21)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM media ORDER BY id").fetchall() == before_media
        assert db.execute("SELECT * FROM message_media ORDER BY message_seq,image_index").fetchall() == before_links
        assert db.execute("SELECT name FROM sqlite_master WHERE name='media_next'").fetchone() is None
        assert db.execute("SELECT name FROM sqlite_master WHERE name='message_media_media'").fetchone() is None


@pytest.mark.parametrize("format_number", [1, 2, 3, 4, 6, 7, 8, 9, 10])
def test_schema_failure_rolls_back_current_step(tmp_path: Path, format_number: int) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / f"v{format_number}-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        if format_number == 1:
            db.execute("CREATE TABLE mind_sessions (sentinel TEXT)")
            db.execute("INSERT INTO mind_sessions VALUES ('untouched')")
        elif format_number == 2:
            db.execute("ALTER TABLE messages ADD COLUMN received_at REAL")
        elif format_number == 3:
            db.execute("ALTER TABLE mind_sessions ADD COLUMN attention_state TEXT")
        elif format_number == 4:
            db.execute("CREATE TABLE message_search (sentinel TEXT)")
        elif format_number == 6:
            db.execute("CREATE TABLE schedules (sentinel TEXT)")
        elif format_number == 7:
            db.execute("ALTER TABLE mind_sessions ADD COLUMN discovered_tools TEXT NOT NULL DEFAULT '[]'")
        elif format_number == 8:
            db.execute("CREATE TABLE web_documents (sentinel TEXT)")
        elif format_number == 10:
            db.execute("ALTER TABLE model_calls ADD COLUMN mind_entry_seq INTEGER")
            db.execute("UPDATE model_calls SET mind_entry_seq=37 WHERE id=1")
        else:
            db.execute("CREATE TABLE image_cache (sentinel TEXT)")
    before = _rows(path)
    web_before = None
    if format_number in (9, 10):
        with sqlite3.connect(path) as db:
            web_before = db.execute("SELECT * FROM web_documents ORDER BY id").fetchall()
    with sqlite3.connect(path) as db:
        model_columns_before = db.execute("PRAGMA table_info(model_calls)").fetchall()
    with pytest.raises(sqlite3.OperationalError):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, format_number)
    assert _rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA table_info(model_calls)").fetchall() == model_columns_before
        if format_number == 10:
            assert db.execute("SELECT mind_entry_seq FROM model_calls WHERE id=1").fetchone() == (37,)
    backup = path.with_name(path.name + f".v{format_number}.bak")
    assert _version(backup) == (0x4C424E31, format_number)
    assert _rows(backup) == before
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA table_info(model_calls)").fetchall() == model_columns_before
    if web_before is not None:
        with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
            assert db.execute("SELECT * FROM web_documents ORDER BY id").fetchall() == web_before
            assert old.execute("SELECT * FROM web_documents ORDER BY id").fetchall() == web_before


def test_v5_invalid_attention_state_rolls_back_without_partial_updates(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v5-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE mind_sessions SET attention_state=? WHERE scene='private:67890'",
            ('{"broken":',),
        )
    before = _rows(path)
    with sqlite3.connect(path) as db:
        states_before = db.execute(
            "SELECT scene,attention_state FROM mind_sessions ORDER BY scene"
        ).fetchall()

    with pytest.raises(ValueError, match=r'Invalid attention_state.*raw=\{"broken":'):
        migrate_database(path)

    assert _version(path) == (0x4C424E31, 5)
    assert _rows(path) == before
    assert _version(tmp_path / "isolated.sqlite3.v5.bak") == (0x4C424E31, 5)
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT scene,attention_state FROM mind_sessions ORDER BY scene"
        ).fetchall() == states_before


def _format22_source(path: Path) -> None:
    """Upgrade the format-21 synthetic source with the committed step, then keep format 22."""
    _format21_source(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 21")
    from len_bot.next import migrate as migration
    with sqlite3.connect(path, isolation_level=None) as db:
        migration._upgrade_one_step(db, path, 21)
    path.with_name(path.name + ".v21.bak").unlink()


def test_format22_reply_effect_tables_keep_all_existing_rows(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format22_source(path)
    with sqlite3.connect(path) as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'message_search%' "
            "AND name!='sqlite_sequence' ORDER BY name")]
        before = {table: db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() for table in tables}
        search_before = db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall()
        assert before["messages"] and before["media"] and before["mind_entries"]
    with pytest.raises(ValueError, match="format 22 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v22.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 22)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            assert db.execute(f"SELECT {_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() == rows
            assert old.execute(f"SELECT {_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == search_before
        for table in ("reply_effects", "reply_effect_calls"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
            assert old.execute("SELECT name FROM sqlite_master WHERE name=?", (table,)).fetchone() is None
    with Store(path) as store:
        assert store.original_image("group:80001", 2, 1)[0] == "image/png"


def test_format22_reply_effect_collision_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format22_source(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE reply_effect_calls (collision TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 22)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE name='reply_effects'").fetchone() is None
        assert db.execute("SELECT name FROM sqlite_master WHERE name='reply_effects_scene'").fetchone() is None


def _format23_source(path: Path) -> None:
    """Upgrade the format-22 synthetic source with the committed step and add daily schedules."""
    _format22_source(path)
    from len_bot.next import migrate as migration
    with sqlite3.connect(path, isolation_level=None) as db:
        migration._upgrade_one_step(db, path, 22)
    path.with_name(path.name + ".v22.bak").unlink()
    with sqlite3.connect(path) as db:
        rows = [
            (101, "group:80001", 1790000100.0, 1790003600.0, "Asia/Shanghai", "合成每日零点", "group",
             "70001", "pending", None, None, None, 0),
            (102, "group:80001", 1790000101.0, 1790007200.0, "Asia/Shanghai", "合成早会", "group",
             "70001", "pending", 1790000200.0, None, None, 545),
            (103, "group:80001", 1790000102.0, 1790010800.0, "America/New_York", "合成晚间", "group",
             "70001", "blocked", 1790000300.0, "合成原始原因", None, 1439),
            (104, "group:80001", 1790000103.0, 1790014400.0, "Asia/Shanghai", "合成每小时", "group",
             "70001", "cancelled", None, None, 3600, None),
        ]
        db.executemany(
            "INSERT INTO schedules(id,scene,created,due_at,timezone,note,target,requester,status,"
            "delivered_at,reason,interval_seconds,cron_minute_of_day) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def test_format23_schedule_cron_text_and_proactive_table_keep_rows(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format23_source(path)
    with sqlite3.connect(path) as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'message_search%' "
            "AND name!='sqlite_sequence' ORDER BY name")]
        before = {table: db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() for table in tables}
        search_before = db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall()
        assert before["messages"] and before["media"] and len(before["schedules"]) >= 4
    with pytest.raises(ValueError, match="format 23 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v23.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 23)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            expected = _daily_cron_rows(rows) if table == "schedules" else rows
            assert db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() == expected
            assert old.execute(f"SELECT rowid,{_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert db.execute("SELECT rowid,search_text FROM message_search ORDER BY rowid").fetchall() == search_before
        assert db.execute("SELECT id,cron FROM schedules WHERE id>100 ORDER BY id").fetchall() == [
            (101, "cron:0 0 * * *"), (102, "cron:5 9 * * *"), (103, "cron:59 23 * * *"), (104, None),
        ]
        assert [row[1] for row in db.execute("PRAGMA table_info(schedules)")][-3:] == ["interval_seconds", "cron", "legacy_source"]
        assert [row[2] for row in db.execute("PRAGMA index_info(schedules_status_due)")] == [
            "scene", "status", "due_at", "id",
        ]
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE schedules SET cron='cron:0 12 * * *' WHERE id=104")
        assert db.execute("SELECT COUNT(*) FROM proactive_wakes").fetchone() == (0,)
        assert old.execute("SELECT name FROM sqlite_master WHERE name='proactive_wakes'").fetchone() is None
    with Store(path) as store:
        assert [(item.id, item.cron, item.status, item.reason) for item in
                (ScheduleStore(store).get_schedule("group:80001", id) for id in (101, 102, 103, 104))] == [
            (101, "cron:0 0 * * *", "pending", None), (102, "cron:5 9 * * *", "pending", None),
            (103, "cron:59 23 * * *", "blocked", "合成原始原因"), (104, None, "cancelled", None),
        ]


def test_format23_proactive_collision_rolls_back_schedule_rebuild(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format23_source(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE proactive_wakes (collision TEXT)")
        schedules = db.execute("SELECT * FROM schedules ORDER BY id").fetchall()
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 23)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT * FROM schedules ORDER BY id").fetchall() == schedules
        assert "cron_minute_of_day" in [row[1] for row in db.execute("PRAGMA table_info(schedules)")]
        assert db.execute("SELECT name FROM sqlite_master WHERE name='schedules_next'").fetchone() is None


def _format24_source(path: Path) -> None:
    """Upgrade the format-23 synthetic source with the committed step and add a proactive wake."""
    _format23_source(path)
    from len_bot.next import migrate as migration
    with sqlite3.connect(path, isolation_level=None) as db:
        migration._upgrade_one_step(db, path, 23)
    path.with_name(path.name + ".v23.bak").unlink()
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO proactive_wakes(scene,turn_id,woke_at,local_date,idle_since,outcome,closed_at) "
                   "VALUES ('group:80001','turn-proactive',1790100000.0,'2026-09-20',1790080000.0,'silent',1790102000.0)")


def test_format24_plugin_events_table_keeps_all_rows(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format24_source(path)
    with sqlite3.connect(path) as db:
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'message_search%' "
            "AND name!='sqlite_sequence' ORDER BY name")]
        before = {table: db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() for table in tables}
        assert before["messages"] and before["proactive_wakes"] and before["schedules"]
    with pytest.raises(ValueError, match="format 24 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v24.bak"
    assert _version(path) == (0x4C424E31, FORMAT_VERSION)
    assert _version(backup) == (0x4C424E31, 24)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table, rows in before.items():
            expected = [(*row, "arrival_count") for row in rows] if table == "proactive_wakes" else rows
            assert db.execute(f"SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid").fetchall() == expected
            assert old.execute(f"SELECT rowid,{_historical_columns(table, old)} FROM {table} ORDER BY rowid").fetchall() == rows
        assert db.execute("SELECT COUNT(*) FROM plugin_events").fetchone() == (0,)
        assert old.execute("SELECT name FROM sqlite_master WHERE name='plugin_events'").fetchone() is None
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO plugin_events(scene,plugin,kind,content,created) "
                       "VALUES ('group:80001','clock','other','x',1.0)")
    with Store(path) as store:
        event = PluginStore(store).add_plugin_event("group:80001", "clock", "event", "合成事件")
        PluginStore(store).add_plugin_event("group:80001", "clock", "reply", "合成回复")
        assert PluginStore(store).plugin_wake_pending("group:80001")
        assert PluginStore(store).pending_plugin_events("group:80001") == [(event, "合成事件"), (event + 1, "合成回复")]


def test_format24_plugin_events_collision_rolls_back(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    _format24_source(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE plugin_events (collision TEXT)")
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 24)
    with sqlite3.connect(path) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(plugin_events)")] == ["collision"]


def _format25_source(path: Path) -> None:
    _format24_source(path)
    from len_bot.next import migrate as migration
    with sqlite3.connect(path, isolation_level=None) as db:
        migration._upgrade_one_step(db, path, 24)
    path.with_name(path.name + '.v24.bak').unlink()


def test_format25_proactive_assessment_preserves_historical_facts(tmp_path):
    path = tmp_path / 'state.db'
    _format25_source(path)
    with sqlite3.connect(path) as db:
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'message_search%' AND name!='sqlite_sequence' ORDER BY name")]
        before = {table: db.execute(f'SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid').fetchall() for table in tables}
    backup = migrate_database(path)
    assert _version(backup) == (0x4C424E31, 25)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as original:
        for table, rows in before.items():
            expected = [(*row, 'arrival_count') for row in rows] if table == 'proactive_wakes' else rows
            assert db.execute(f'SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid').fetchall() == expected
            assert original.execute(f'SELECT rowid,{_historical_columns(table, original)} FROM {table} ORDER BY rowid').fetchall() == rows
        assert [row[2] for row in db.execute('PRAGMA index_info(reply_effects_turn)')] == ['scene', 'turn_id']
    with Store(path) as store:
        turn = store.start_turn('group:80001', proactive=('synthetic new wake', '2026-09-29', 1790100100.0))
        assert store.db.execute('SELECT assessment FROM proactive_wakes WHERE turn_id=?', (turn,)).fetchone()[0] == 'reply_effects'


def test_format25_assessment_collision_rolls_back(tmp_path):
    path = tmp_path / 'state.db'
    _format25_source(path)
    with sqlite3.connect(path) as db:
        db.execute('CREATE INDEX reply_effects_turn ON reply_effects(scene)')
        before = db.execute('SELECT * FROM proactive_wakes').fetchall()
    with pytest.raises(sqlite3.OperationalError, match='already exists'):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 25)
    with sqlite3.connect(path) as db:
        assert 'assessment' not in [row[1] for row in db.execute('PRAGMA table_info(proactive_wakes)')]
        assert db.execute('SELECT * FROM proactive_wakes').fetchall() == before


def test_format26_audio_cache_preserves_existing_data(tmp_path):
    path = tmp_path / 'state.db'
    with Store(path) as store:
        store.append('group:80001', {'role': 'user', 'content': '迁移前原话'})
    with sqlite3.connect(path) as db:
        _remove_browser_columns(db)
        db.execute('DROP TABLE audio_cache')
        db.execute('DROP TABLE audio_calls')
        db.execute('DROP TABLE notices')
        _remove_format31_indexes(db)
        db.execute('PRAGMA user_version=26')
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'message_search%' AND name!='sqlite_sequence'")]
        before = {table: db.execute(f'SELECT rowid,{_historical_columns(table, db)} FROM {table} ORDER BY rowid').fetchall() for table in tables}
    backup = migrate_database(path)
    assert _version(backup) == (0x4C424E31, 26)
    with Store(path) as store, sqlite3.connect(backup) as original:
        for table, rows in before.items():
            assert [tuple(row) for row in store.db.execute(f'SELECT rowid,{_historical_columns(table, store.db)} FROM {table} ORDER BY rowid')] == rows
            assert original.execute(f'SELECT rowid,{_historical_columns(table, original)} FROM {table} ORDER BY rowid').fetchall() == rows
        assert store.db.execute('SELECT COUNT(*) FROM audio_cache').fetchone()[0] == 0
        assert original.execute("SELECT name FROM sqlite_master WHERE name='audio_cache'").fetchone() is None


def test_format26_audio_cache_collision_rolls_back(tmp_path):
    path = tmp_path / 'state.db'
    with Store(path):
        pass
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE notices')
        _remove_format31_indexes(db)
        db.execute('PRAGMA user_version=26')
    with pytest.raises(sqlite3.OperationalError, match='already exists'):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 26)


def _format27_source(path):
    with Store(path) as store:
        store.append('group:80001', {'role': 'user', 'content': '真实保存的旧条目'})
    with sqlite3.connect(path) as db:
        _remove_browser_columns(db)
        db.execute('DROP TABLE audio_cache')
        db.execute('DROP TABLE audio_calls')
        db.execute('CREATE TABLE audio_cache (scene TEXT NOT NULL,platform_id TEXT NOT NULL,audio_index INTEGER NOT NULL,'
                   'wav BLOB NOT NULL,duration REAL NOT NULL,fetched_at REAL NOT NULL,'
                   'transcript TEXT,provider TEXT,model TEXT,transcribed_at REAL,PRIMARY KEY(scene,platform_id,audio_index))')
        db.executemany('INSERT INTO audio_cache VALUES (?,?,?,?,?,?,?,?,?,?)', [
            ('group:80001','7001',1,b'synthetic-existing-wav',0.1,100,'旧成功文字','fixture','exact-audio',105),
            ('group:80001','7002',1,b'synthetic-unfinished-wav',0.2,101,None,None,None,None)])
        db.execute('DROP TABLE notices')
        _remove_format31_indexes(db)
        db.execute('PRAGMA user_version=27')


def test_format27_audio_processing_preserves_bytes_and_success(tmp_path):
    path=tmp_path/'state.db';_format27_source(path)
    backup=migrate_database(path)
    with sqlite3.connect(backup) as original, Store(path) as store:
        old=original.execute('SELECT * FROM audio_cache ORDER BY platform_id').fetchall()
        fields=','.join(row[1] for row in original.execute('PRAGMA table_info(audio_cache)'))
        assert [tuple(row) for row in store.db.execute(f'SELECT {fields} FROM audio_cache ORDER BY platform_id')]==old
        assert [tuple(row) for row in store.db.execute('SELECT status,error,announced_at FROM audio_cache ORDER BY platform_id')]==[
            ('complete',None,105),('idle',None,None)]
        assert original.execute('PRAGMA user_version').fetchone()[0]==27
        assert store.db.execute('SELECT count(*) FROM audio_calls').fetchone()[0]==0
        assert [tuple(row) for row in store.db.execute('SELECT * FROM mind_entries')]==original.execute('SELECT * FROM mind_entries').fetchall()


def test_format27_audio_processing_conflict_rolls_back(tmp_path):
    path=tmp_path/'state.db';_format27_source(path)
    with sqlite3.connect(path) as db:
        rows=db.execute('SELECT * FROM audio_cache').fetchall()
        db.execute('CREATE TABLE audio_calls(collision TEXT)')
    with pytest.raises(sqlite3.OperationalError,match='already exists'):
        migrate_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==27
        assert db.execute('SELECT * FROM audio_cache').fetchall()==rows
        assert 'status' not in [row[1] for row in db.execute('PRAGMA table_info(audio_cache)')]


def test_format28_browser_binding_preserves_existing_tasks(tmp_path):
    path = tmp_path / 'state.db'
    with Store(path) as store:
        item = TaskStore(store).create('group:80001', '70001', '原目标', '原交付', '原上下文', '原输入')
    with sqlite3.connect(path) as db:
        _remove_browser_columns(db)
        db.execute('DROP TABLE notices')
        _remove_format31_indexes(db)
        db.execute('PRAGMA user_version=28')
        before = db.execute(f'SELECT {TASK_V28_COLUMNS} FROM tasks').fetchall()
    backup = migrate_database(path)
    with Store(path) as store, sqlite3.connect(backup) as old:
        assert [tuple(row) for row in store.db.execute(f'SELECT {TASK_V28_COLUMNS} FROM tasks')] == before
        assert old.execute(f'SELECT {TASK_V28_COLUMNS} FROM tasks').fetchall() == before
        result = TaskStore(store).get(item.scene, item.id)
        assert not result.account_browser and not result.browser_active and result.browser_session is None


def test_format29_adds_notice_storage_without_rewriting_original_messages(tmp_path):
    path = tmp_path / 'notices.sqlite3'
    shutil.copyfile(FIXTURES / 'v6-synthetic.sqlite3', path)
    migrate_database(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE notices')
        _remove_format31_indexes(db)
        before = db.execute('SELECT * FROM messages').fetchall()
        db.execute('PRAGMA user_version=29')
    # Keep the earlier fixture migration backup, but this explicit new input needs its own backup name.
    for version in range(29, FORMAT_VERSION):
        path.with_name(path.name + f'.v{version}.bak').unlink()
    migrate_database(path)
    with sqlite3.connect(path) as db:
        assert db.execute(f'SELECT {_historical_columns("messages", db)} FROM messages').fetchall() == before
        assert db.execute('SELECT COUNT(*) FROM notices').fetchone()[0] == 0
        assert db.execute('PRAGMA user_version').fetchone()[0] == FORMAT_VERSION


def test_format31_audio_cost_migration_preserves_actual_exchanges(tmp_path):
    path=tmp_path/'state.db'
    with Store(path) as store:
        store.db.execute("INSERT INTO audio_calls(scene,platform_id,audio_index,started,ended,request,response,usage) "
                         "VALUES(?,?,?,?,?,?,?,?)",('group:80001','123',1,1,2,'{}','{"text":"合成原响应"}','{"type":"duration","seconds":4}'))
        store.db.commit()
    with sqlite3.connect(path) as db:
        _remove_format32_to35_additions(db)
        db.execute('ALTER TABLE audio_calls DROP COLUMN cost')
        db.execute('PRAGMA user_version=31')
        before=db.execute('SELECT * FROM audio_calls').fetchall()
    backup=migrate_database(path)
    with Store(path) as store,sqlite3.connect(backup) as original:
        assert original.execute('SELECT * FROM audio_calls').fetchall()==before
        actual=[tuple(row) for row in store.db.execute('SELECT * FROM audio_calls')]
        assert actual==[row+(None,) for row in before]


def test_migration_command_for_initialized_but_unstarted_instance(tmp_path):
    import subprocess
    import sys
    from len_bot.next.config import load_host_config
    from len_bot.next.setup import FirstSetup, initialize

    sample = Path(__file__).resolve().parents[1] / 'deploy/current/first-setup.example.json'
    initialize(tmp_path, FirstSetup.model_validate_json(sample.read_bytes()))
    config = load_host_config(tmp_path)
    config_bytes = (tmp_path / 'lenbot.config.json').read_bytes()
    result = subprocess.run([sys.executable, '-m', 'len_bot.next.migrate'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'nothing created or migrated' in result.stdout
    assert not config.database.exists()
    assert (tmp_path / 'lenbot.config.json').read_bytes() == config_bytes

    config.database.parent.mkdir(parents=True)
    config.database.write_bytes(b'not an sqlite database; preserve this input')
    broken = subprocess.run([sys.executable, '-m', 'len_bot.next.migrate'], cwd=tmp_path,
                            capture_output=True, text=True)
    assert broken.returncode != 0
    assert 'file is not a database' in broken.stderr
    assert config.database.read_bytes() == b'not an sqlite database; preserve this input'
