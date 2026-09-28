"""Explicit stopped-instance upgrade of the memory processing database to the current format."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import sys

from .config import load_instance_config
from .memory_jobs import FORMAT_VERSION


APPLICATION_ID = 0x4C424D4A


def _step(db: sqlite3.Connection, path: Path, version: int) -> Path:
    """Keep one SQLite copy of this format, then apply one step atomically."""
    backup_path = path.with_name(path.name + f".v{version}.bak")
    with backup_path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(backup_path)) as backup:
            db.backup(backup)
    except BaseException:
        backup_path.unlink()
        raise
    try:
        db.execute("BEGIN EXCLUSIVE")
        actual = (db.execute("PRAGMA application_id").fetchone()[0],
                  db.execute("PRAGMA user_version").fetchone()[0])
        if actual != (APPLICATION_ID, version):
            raise ValueError(f"Memory processing database changed before migration: {path}")
        if version == 1:
            db.execute(
                "CREATE TABLE memory_exclusions ("
                "scene TEXT NOT NULL, message_seq INTEGER NOT NULL,"
                "PRIMARY KEY(scene,message_seq))"
            )
        elif version == 2:
            db.execute(
                "CREATE TABLE memory_summary_runs ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, scope TEXT NOT NULL, path TEXT NOT NULL,"
                "started REAL NOT NULL, ended REAL,"
                "status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),"
                "request TEXT NOT NULL, response TEXT, usage TEXT, cost TEXT, error TEXT)"
            )
            db.execute("CREATE INDEX memory_summary_runs_path ON memory_summary_runs(scope,path,id)")
        elif version == 3:
            db.execute("ALTER TABLE memory_summary_runs ADD COLUMN model_started REAL")
            db.execute("CREATE TABLE memory_embedding_calls ("
                       "id INTEGER PRIMARY KEY,scene TEXT NOT NULL,purpose TEXT NOT NULL,"
                       "started REAL NOT NULL,ended REAL,request TEXT NOT NULL,response TEXT,usage TEXT,cost TEXT,error TEXT)")
            db.execute("CREATE INDEX memory_embedding_usage ON memory_embedding_calls(started,scene)")
        db.execute(f"PRAGMA user_version={version + 1}")
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return backup_path


def migrate_memory_jobs(path: Path) -> Path:
    """Upgrade format 1 or 2 step by step; return the copy of the input format."""
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as db:
        application_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if application_id != APPLICATION_ID or version not in range(1, FORMAT_VERSION):
            raise ValueError(
                f"Expected a memory processing format 1 through {FORMAT_VERSION - 1} database: {path}; "
                f"found app={application_id}, version={version}")
        for step in range(version, FORMAT_VERSION):
            backup = path.with_name(path.name + f".v{step}.bak")
            if backup.exists():
                raise FileExistsError(f"Migration backup already exists: {backup}")
        copies = [_step(db, path, step) for step in range(version, FORMAT_VERSION)]
    return copies[0]


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Memory processing migration takes no arguments; stop the instance and run from its root")
    config = load_instance_config(Path.cwd())
    paths = [config.database.with_name(config.database.name + ".memory.sqlite3")]
    paths.extend(sorted((Path.cwd() / '.runtime' / 'chat-tests').glob('*/state.db.memory.sqlite3')))
    for path in paths:
        if not path.exists():
            continue
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
            app, version = (db.execute('PRAGMA application_id').fetchone()[0],
                                     db.execute('PRAGMA user_version').fetchone()[0])
        if app == APPLICATION_ID and version == FORMAT_VERSION:
            print(f"Already current: {path}")
            continue
        backup = migrate_memory_jobs(path)
        print(f"Offline memory processing migration completed; input-format copy: {backup}")



if __name__ == "__main__":
    main()
