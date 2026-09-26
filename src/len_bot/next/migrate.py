"""Explicit offline upgrade of an isolated next-core database from format 1 to 2."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
import sys

from .config import load_config


APPLICATION_ID = 0x4C424E31


def migrate_database(path: Path) -> Path:
    """Back up format 1, then atomically add the scene compression read state."""
    path = Path(path).resolve()
    backup_path = path.with_name(path.name + ".v1.bak")
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as db:
        application_id = db.execute("PRAGMA application_id").fetchone()[0]
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if application_id != APPLICATION_ID or version != 1:
            raise ValueError(f"Expected a next-core format 1 database: {path}; found app={application_id}, version={version}")
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
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if application_id != APPLICATION_ID or version != 1:
                raise ValueError(f"Database changed before migration: {path}; app={application_id}, version={version}")
            db.execute(
                "CREATE TABLE mind_sessions ("
                "scene TEXT PRIMARY KEY, compact_through INTEGER NOT NULL, recap TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version = 2")
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return backup_path


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Migration takes no arguments; run from the isolated instance directory")
    config = load_config(Path.cwd())
    backup = migrate_database(config.database)
    print(f"Offline migration completed; original format 1 copy: {backup}")


if __name__ == "__main__":
    main()
