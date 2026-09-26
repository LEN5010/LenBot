"""Explicit offline upgrade of an isolated next-core database to format 8."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys

from .config import load_config
from .messages import plain_text
from .store import Store, encode


APPLICATION_ID = 0x4C424E31


def _format(db: sqlite3.Connection) -> tuple[int, int]:
    return (db.execute("PRAGMA application_id").fetchone()[0],
            db.execute("PRAGMA user_version").fetchone()[0])


def _upgrade_one_step(db: sqlite3.Connection, path: Path, version: int) -> None:
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
        if _format(db) != (APPLICATION_ID, version):
            raise ValueError(f"Database changed before migration step {version}: {path}")
        if version == 1:
            db.execute(
                "CREATE TABLE mind_sessions ("
                "scene TEXT PRIMARY KEY, compact_through INTEGER NOT NULL, recap TEXT NOT NULL)"
            )
        elif version == 2:
            db.execute("ALTER TABLE messages ADD COLUMN received_at REAL")
            db.execute(
                "CREATE TABLE mind_sessions_next ("
                "scene TEXT PRIMARY KEY, compact_through INTEGER NOT NULL DEFAULT 0, "
                "recap TEXT, last_message_seq INTEGER NOT NULL DEFAULT 0)"
            )
            db.execute(
                "INSERT INTO mind_sessions_next(scene,compact_through,recap,last_message_seq) "
                "SELECT s.scene,s.compact_through,s.recap,"
                "COALESCE((SELECT MAX(m.seq) FROM messages m WHERE m.scene=s.scene),0) "
                "FROM mind_sessions s"
            )
            db.execute(
                "INSERT OR IGNORE INTO mind_sessions_next(scene,last_message_seq) "
                "SELECT scene,MAX(seq) FROM messages GROUP BY scene"
            )
            db.execute("DROP TABLE mind_sessions")
            db.execute("ALTER TABLE mind_sessions_next RENAME TO mind_sessions")
        elif version == 3:
            db.execute("ALTER TABLE mind_sessions ADD COLUMN attention_state TEXT")
        elif version == 4:
            db.execute(
                "CREATE VIRTUAL TABLE message_search USING fts5("
                "search_text, tokenize='trigram case_sensitive 1')"
            )
            for seq, body in db.execute("SELECT seq,body FROM messages ORDER BY seq"):
                db.execute(
                    "INSERT INTO message_search(rowid,search_text) VALUES (?,?)",
                    (seq, plain_text(Store._message(body)).casefold()),
                )
        elif version == 5:
            for scene, raw_state in db.execute(
                "SELECT scene,attention_state FROM mind_sessions "
                "WHERE attention_state IS NOT NULL"
            ).fetchall():
                try:
                    state = json.loads(raw_state)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"Invalid attention_state for {scene}: {error}; raw={raw_state[:500]}"
                    ) from error
                state["quiet_notice_until"] = None
                db.execute(
                    "UPDATE mind_sessions SET attention_state=? WHERE scene=?",
                    (encode(state), scene),
                )
        elif version == 6:
            db.execute(
                "CREATE TABLE schedules ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL,"
                "created REAL NOT NULL, due_at REAL NOT NULL,"
                "timezone TEXT NOT NULL, note TEXT NOT NULL,"
                "target TEXT NOT NULL, requester TEXT,"
                "status TEXT NOT NULL, delivered_at REAL, reason TEXT)"
            )
            db.execute(
                "CREATE INDEX schedules_status_due ON schedules(scene,status,due_at,id)"
            )
        else:
            db.execute(
                "ALTER TABLE mind_sessions ADD COLUMN discovered_tools TEXT NOT NULL DEFAULT '[]'"
            )
        db.execute(f"PRAGMA user_version = {version + 1}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def migrate_database(path: Path) -> Path:
    """Upgrade format 1 through 7 while retaining a copy of each step."""
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as db:
        application_id, version = _format(db)
        if application_id != APPLICATION_ID or version not in (1, 2, 3, 4, 5, 6, 7):
            raise ValueError(
                f"Expected a next-core format 1, 2, 3, 4, 5, 6 or 7 database: {path}; "
                f"found app={application_id}, version={version}"
            )
        for step in range(version, 8):
            backup = path.with_name(path.name + f".v{step}.bak")
            if backup.exists():
                raise FileExistsError(f"Migration backup already exists: {backup}")
        original_backup = path.with_name(path.name + f".v{version}.bak")
        for step in range(version, 8):
            _upgrade_one_step(db, path, step)
    return original_backup


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Migration takes no arguments; run from the isolated instance directory")
    config = load_config(Path.cwd())
    backup = migrate_database(config.database)
    print(f"Offline migration completed; input-format copy: {backup}")


if __name__ == "__main__":
    main()
