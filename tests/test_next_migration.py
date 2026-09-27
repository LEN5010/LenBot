"""Offline format upgrades using committed DDL with synthetic, content-replaced rows."""

import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from len_bot.next.migrate import migrate_database
from len_bot.next.store import Store


FIXTURES = Path(__file__).parent / "fixtures" / "next" / "migration"
MESSAGE_COLUMNS = ("seq", "scene", "platform_id", "body", "raw")
MODEL_CALL_COLUMNS = (
    "id", "turn_id", "role", "started", "ended", "request", "response", "usage", "error",
)
TURN_COLUMNS = ("id", "scene", "started", "ended", "status", "error")
FIRST_EXPRESSION_COLUMNS = (
    "wake_received_at", "first_expression_at", "first_expression_delivery",
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
    assert _version(path) == (0x4C424E31, 14)
    assert _version(original_backup) == (0x4C424E31, format_number)
    for intermediate_format in range(format_number + 1, 13):
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
            assert store.list_schedules("group:12345", status="all") == []
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
            )} == {"schedules_status_due"}


def test_v7_discovery_upgrade_preserves_actual_records_and_starts_empty(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v7-synthetic.sqlite3", path)
    assert _version(path) == (0x4C424E31, 7)
    with pytest.raises(ValueError, match="format 7 requires offline migration"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v7.bak"
    assert _version(path) == (0x4C424E31, 14)
    assert _version(backup) == (0x4C424E31, 7)
    assert _version(tmp_path / "isolated.sqlite3.v8.bak") == (0x4C424E31, 8)
    assert _version(tmp_path / "isolated.sqlite3.v9.bak") == (0x4C424E31, 9)
    assert _version(tmp_path / "isolated.sqlite3.v10.bak") == (0x4C424E31, 10)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table in ("messages", "mind_entries", "turns", "schedules"):
            columns = ",".join(TURN_COLUMNS) if table == "turns" else "*"
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == old.execute(
                f"SELECT {columns} FROM {table} ORDER BY rowid"
            ).fetchall()
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
    assert _version(path) == (0x4C424E31, 14)
    assert _version(backup) == (0x4C424E31, 8)
    assert _version(tmp_path / "isolated.sqlite3.v9.bak") == (0x4C424E31, 9)
    assert _version(tmp_path / "isolated.sqlite3.v10.bak") == (0x4C424E31, 10)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table in ("messages", "mind_entries", "turns", "mind_sessions", "schedules"):
            columns = ",".join(TURN_COLUMNS) if table == "turns" else "*"
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == old.execute(
                f"SELECT {columns} FROM {table} ORDER BY rowid"
            ).fetchall()
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
    assert _version(path) == (0x4C424E31, 14)
    assert _version(backup) == (0x4C424E31, 9)
    assert _version(tmp_path / "isolated.sqlite3.v10.bak") == (0x4C424E31, 10)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        for table in ("messages", "mind_entries", "turns", "mind_sessions", "schedules", "web_documents"):
            columns = ",".join(TURN_COLUMNS) if table == "turns" else "*"
            assert db.execute(f"SELECT {columns} FROM {table} ORDER BY rowid").fetchall() == old.execute(
                f"SELECT {columns} FROM {table} ORDER BY rowid"
            ).fetchall()
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
            table: old.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
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
    assert _version(path) == (0x4C424E31, 14)
    assert _version(backup) == (0x4C424E31, 10)
    assert _rows(path) == before == _rows(backup)
    assert _version(tmp_path / "isolated.sqlite3.v11.bak") == (0x4C424E31, 11)
    assert _version(tmp_path / "isolated.sqlite3.v12.bak") == (0x4C424E31, 12)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert [row[1] for row in db.execute("PRAGMA table_info(model_calls)")] == [
            *MODEL_CALL_COLUMNS, "mind_entry_seq", "cost",
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
            assert db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == rows
            assert old.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall() == rows
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
    assert _version(path) == (0x4C424E31, 14)
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
    assert _version(path) == (0x4C424E31, 14)
    assert _version(backup) == (0x4C424E31, 12)
    assert _rows(path) == before == _rows(backup)
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert [row[1] for row in db.execute("PRAGMA table_info(model_calls)")] == [
            *MODEL_CALL_COLUMNS, "mind_entry_seq", "cost",
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
    assert _version(path) == (0x4C424E31, 14)
    assert _rows(path) == before == _rows(backup)
    with Store(path) as store:
        assert [tuple(row) for row in store.db.execute(
            "SELECT rowid,* FROM model_calls ORDER BY rowid")] == calls_before
        for table in ("tasks", "task_events", "task_files"):
            assert store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_migration_rejects_current_and_wrong_database(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v2-synthetic.sqlite3", path)
    migrate_database(path)
    with pytest.raises(ValueError, match="Expected a next-core format 1 through 13 database"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 14)

    unrelated = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(unrelated) as db:
        db.execute("CREATE TABLE other (value TEXT)")
        db.execute("INSERT INTO other VALUES ('untouched')")
    with pytest.raises(ValueError, match="Expected a next-core format 1 through 13 database"):
        migrate_database(unrelated)
    assert not unrelated.with_name(unrelated.name + ".v1.bak").exists()
    with sqlite3.connect(unrelated) as db:
        assert db.execute("SELECT value FROM other").fetchone()[0] == "untouched"


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
