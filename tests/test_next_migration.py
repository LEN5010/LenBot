"""Offline format upgrades using committed DDL with synthetic, content-replaced rows."""

import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from len_bot.next.migrate import migrate_database
from len_bot.next.store import Store


FIXTURES = Path(__file__).parent / "fixtures" / "next" / "migration"
ORIGINAL_TABLES = ("messages", "mind_entries", "turns", "model_calls")


def _rows(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ORIGINAL_TABLES}


def _version(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as db:
        return (db.execute("PRAGMA application_id").fetchone()[0],
                db.execute("PRAGMA user_version").fetchone()[0])


def _old_columns(path: Path) -> dict[str, list[tuple]]:
    rows = _rows(path)
    return {table: ([row[:5] for row in values] if table == "messages" else values)
            for table, values in rows.items()}


@pytest.mark.parametrize("format_number", [1, 2, 3])
def test_old_format_upgrades_without_changing_original_records(tmp_path: Path, format_number: int) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / f"v{format_number}-synthetic.sqlite3", path)
    before = _old_columns(path)
    assert _version(path) == (0x4C424E31, format_number)
    with pytest.raises(ValueError, match=f"format {format_number} requires offline migration"):
        Store(path)

    original_backup = migrate_database(path)
    assert original_backup == tmp_path / f"isolated.sqlite3.v{format_number}.bak"
    assert _version(path) == (0x4C424E31, 4)
    assert _version(original_backup) == (0x4C424E31, format_number)
    assert _old_columns(path) == before == _old_columns(original_backup)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM mind_sessions WHERE attention_state IS NOT NULL").fetchone()[0] == 0
        if format_number < 3:
            assert db.execute("SELECT count(*) FROM messages WHERE received_at IS NOT NULL").fetchone()[0] == 0
    if format_number == 1:
        intermediate = tmp_path / "isolated.sqlite3.v2.bak"
        assert _version(intermediate) == (0x4C424E31, 2)
        assert _old_columns(intermediate) == before
        assert _version(tmp_path / "isolated.sqlite3.v3.bak") == (0x4C424E31, 3)
        with Store(path) as store:
            assert store.pending_messages("group:12345") == []
            assert store.load_attention("group:12345") is None
            assert store.active_history("group:12345") == (
                None, [(row[0], json.loads(row[2])) for row in before["mind_entries"]]
            )
    elif format_number == 2:
        assert _version(tmp_path / "isolated.sqlite3.v3.bak") == (0x4C424E31, 3)
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
    else:
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


def test_migration_rejects_current_and_wrong_database(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / "v2-synthetic.sqlite3", path)
    migrate_database(path)
    with pytest.raises(ValueError, match="Expected a next-core format 1, 2 or 3 database"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 4)

    unrelated = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(unrelated) as db:
        db.execute("CREATE TABLE other (value TEXT)")
        db.execute("INSERT INTO other VALUES ('untouched')")
    with pytest.raises(ValueError, match="Expected a next-core format 1, 2 or 3 database"):
        migrate_database(unrelated)
    assert not unrelated.with_name(unrelated.name + ".v1.bak").exists()
    with sqlite3.connect(unrelated) as db:
        assert db.execute("SELECT value FROM other").fetchone()[0] == "untouched"


@pytest.mark.parametrize("format_number", [1, 2, 3])
def test_schema_failure_rolls_back_current_step(tmp_path: Path, format_number: int) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURES / f"v{format_number}-synthetic.sqlite3", path)
    with sqlite3.connect(path) as db:
        if format_number == 1:
            db.execute("CREATE TABLE mind_sessions (sentinel TEXT)")
            db.execute("INSERT INTO mind_sessions VALUES ('untouched')")
        elif format_number == 2:
            db.execute("ALTER TABLE messages ADD COLUMN received_at REAL")
        else:
            db.execute("ALTER TABLE mind_sessions ADD COLUMN attention_state TEXT")
    before = _rows(path)
    with pytest.raises(sqlite3.OperationalError):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, format_number)
    assert _rows(path) == before
    backup = path.with_name(path.name + f".v{format_number}.bak")
    assert _version(backup) == (0x4C424E31, format_number)
    assert _rows(backup) == before
