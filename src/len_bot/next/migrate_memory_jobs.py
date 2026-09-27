"""Explicit stopped-instance upgrade of memory processing format 1 to 2."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import sys

from .config import load_instance_config


APPLICATION_ID = 0x4C424D4A


def migrate_memory_jobs(path: Path) -> Path:
    """Keep one SQLite copy, then add selected-source exclusions atomically."""
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as db:
        application_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if (application_id, version) != (APPLICATION_ID, 1):
            raise ValueError(
                f"Expected a memory processing format 1 database: {path}; "
                f"found app={application_id}, version={version}")

        backup_path = path.with_name(path.name + ".v1.bak")
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
            if actual != (APPLICATION_ID, 1):
                raise ValueError(f"Memory processing database changed before migration: {path}")
            db.execute(
                "CREATE TABLE memory_exclusions ("
                "scene TEXT NOT NULL, message_seq INTEGER NOT NULL,"
                "PRIMARY KEY(scene,message_seq))"
            )
            db.execute("PRAGMA user_version=2")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return backup_path


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Memory processing migration takes no arguments; stop the instance and run from its root")
    config = load_instance_config(Path.cwd())
    path = config.database.with_name(config.database.name + ".memory.sqlite3")
    backup = migrate_memory_jobs(path)
    print(f"Offline memory processing migration completed; format 1 copy: {backup}")


if __name__ == "__main__":
    main()
