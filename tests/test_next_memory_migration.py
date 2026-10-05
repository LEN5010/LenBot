"""Offline memory data migrations: processing database format and legacy ledger import."""

from pathlib import Path
import sqlite3

import pytest

from len_bot.next.memory.jobs import FORMAT_VERSION, MemoryJobs
from len_bot.next.maintenance.migrate_memory_jobs import migrate_memory_jobs


APPLICATION_ID = 0x4C424D4A


def _format1(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(f"""
            PRAGMA application_id={APPLICATION_ID};
            PRAGMA user_version=1;
            CREATE TABLE memory_cursors (scene TEXT PRIMARY KEY, after_seq INTEGER NOT NULL, enabled_at REAL NOT NULL);
            CREATE TABLE memory_jobs (
                id INTEGER PRIMARY KEY, scene TEXT NOT NULL, backend TEXT NOT NULL,
                first_seq INTEGER NOT NULL, through_seq INTEGER NOT NULL,
                status TEXT NOT NULL, started REAL NOT NULL, ended REAL,
                details TEXT NOT NULL, error TEXT);
            CREATE INDEX memory_jobs_scene ON memory_jobs(scene,id);
            INSERT INTO memory_cursors VALUES('onebot:group:80001', 12, 1790000000.0);
            INSERT INTO memory_jobs VALUES(3,'onebot:group:80001','local',5,12,'complete',1790000001.0,1790000002.0,
                '{{"calls":[],"writes":[{{"path":"people/70001/profile.md"}}],"tools":[]}}',NULL);
        """)


def _rows(path: Path) -> dict:
    with sqlite3.connect(path) as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
                for table in ("memory_cursors", "memory_jobs")}


@pytest.mark.parametrize("start", [1, 2])
def test_memory_processing_upgrade_keeps_rows_and_adds_summary_runs(tmp_path, start):
    path = tmp_path / "state.db.memory.sqlite3"
    _format1(path)
    if start == 2:
        migrate_memory_jobs(path)
        for version in range(1, FORMAT_VERSION):
            path.with_name(path.name + f".v{version}.bak").unlink()
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE memory_embedding_calls")
            db.execute("DROP TABLE memory_summary_runs")
            db.execute("DROP TABLE memory_personas")
            db.execute("PRAGMA user_version=2")
            db.execute("INSERT INTO memory_exclusions VALUES('onebot:group:80001', 7)")
    before = _rows(path)
    with pytest.raises(ValueError, match=f"format {start} requires offline migration"):
        MemoryJobs(path)

    backup = migrate_memory_jobs(path)
    assert backup == path.with_name(path.name + f".v{start}.bak")
    with sqlite3.connect(path) as db, sqlite3.connect(backup) as old:
        assert db.execute("PRAGMA user_version").fetchone()[0] == FORMAT_VERSION
        assert old.execute("PRAGMA user_version").fetchone()[0] == start
        assert db.execute("SELECT COUNT(*) FROM memory_summary_runs").fetchone() == (0,)
        assert db.execute("SELECT * FROM memory_exclusions").fetchall() == (
            [("onebot:group:80001", 7)] if start == 2 else [])
    assert _rows(path) == before == _rows(backup)
    with MemoryJobs(path) as jobs:
        assert jobs.after("onebot:group:80001") == 12
        run = jobs.begin_summary("onebot:group:80001", "onebot:group:80001", "people", {"messages": []})
        jobs.recover_summaries()
        assert jobs.summary_runs("onebot:group:80001", "people")[0]["status"] == "interrupted"
        assert jobs.summary_run(run)["request"] == {"messages": []}


def test_memory_processing_collision_rolls_back_and_backup_is_not_overwritten(tmp_path):
    path = tmp_path / "state.db.memory.sqlite3"
    _format1(path)
    migrate_memory_jobs(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(FileExistsError, match="v2.bak"):
        migrate_memory_jobs(path)
    for version in range(2, FORMAT_VERSION):
        path.with_name(path.name + f".v{version}.bak").unlink()
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_memory_jobs(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
