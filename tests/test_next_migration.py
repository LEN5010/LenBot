"""Format-1 to format-2 conversion using the committed, content-replaced schema fixture."""

from pathlib import Path
import json
import shutil
import sqlite3

import pytest

from len_bot.next.migrate import migrate_database
from len_bot.next.store import Store


FIXTURE = Path(__file__).parent / "fixtures" / "next" / "migration" / "v1-synthetic.sqlite3"
OLD_TABLES = ("messages", "mind_entries", "turns", "model_calls")


def _rows(path: Path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in OLD_TABLES}


def _version(path: Path) -> tuple[int, int]:
    with sqlite3.connect(path) as db:
        return (db.execute("PRAGMA application_id").fetchone()[0],
                db.execute("PRAGMA user_version").fetchone()[0])


def test_format_one_preserved_and_explicitly_upgraded(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURE, path)
    before = _rows(path)
    assert _version(path) == (0x4C424E31, 1)
    with pytest.raises(ValueError, match="offline migration while stopped"):
        Store(path)

    backup = migrate_database(path)
    assert backup == tmp_path / "isolated.sqlite3.v1.bak"
    assert _version(path) == (0x4C424E31, 2)
    assert _version(backup) == (0x4C424E31, 1)
    assert _rows(path) == before == _rows(backup)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM mind_sessions").fetchone()[0] == 0
    with Store(path) as store:
        expected = [(row[0], json.loads(row[2])) for row in before["mind_entries"]]
        assert store.active_history("group:12345") == (None, expected)
        assert store.entries("group:12345") == [entry for _, entry in expected]


def test_migration_refuses_repetition_and_existing_backup(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURE, path)
    backup = path.with_name(path.name + ".v1.bak")
    backup.write_bytes(b"existing backup must remain untouched")
    before = _rows(path)
    with pytest.raises(FileExistsError):
        migrate_database(path)
    assert backup.read_bytes() == b"existing backup must remain untouched"
    assert _version(path) == (0x4C424E31, 1)
    assert _rows(path) == before

    backup.unlink()
    migrate_database(path)
    with pytest.raises(ValueError, match="Expected a next-core format 1 database"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 2)


def test_wrong_database_rejected_without_backup(tmp_path: Path) -> None:
    path = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE other (value TEXT)")
        db.execute("INSERT INTO other VALUES ('untouched')")
    with pytest.raises(ValueError, match="Expected a next-core format 1 database"):
        migrate_database(path)
    assert not path.with_name(path.name + ".v1.bak").exists()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM other").fetchone()[0] == "untouched"


def test_schema_failure_rolls_back_original_database(tmp_path: Path) -> None:
    path = tmp_path / "isolated.sqlite3"
    shutil.copyfile(FIXTURE, path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE mind_sessions (sentinel TEXT)")
        db.execute("INSERT INTO mind_sessions VALUES ('untouched')")
    before = _rows(path)
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_database(path)
    assert _version(path) == (0x4C424E31, 1)
    assert _rows(path) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT sentinel FROM mind_sessions").fetchone()[0] == "untouched"
    backup = path.with_name(path.name + ".v1.bak")
    assert _version(backup) == (0x4C424E31, 1)
    assert _rows(backup) == before
