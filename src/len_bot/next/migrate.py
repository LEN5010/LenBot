"""Explicit offline upgrade of an isolated next-core database to format 19."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import sqlite3
import sys

from .config import load_instance_config
from .messages import plain_text
from .store import FORMAT_VERSION, Store, encode


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
        elif version == 7:
            db.execute(
                "ALTER TABLE mind_sessions ADD COLUMN discovered_tools TEXT NOT NULL DEFAULT '[]'"
            )
        elif version == 8:
            db.execute(
                "CREATE TABLE web_documents ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, body TEXT NOT NULL)"
            )
        elif version == 9:
            db.execute(
                "CREATE TABLE image_cache ("
                "scene TEXT NOT NULL, platform_id TEXT NOT NULL,"
                "image_index INTEGER NOT NULL, jpeg BLOB NOT NULL,"
                "width INTEGER NOT NULL, height INTEGER NOT NULL,"
                "animated INTEGER NOT NULL, fetched_at REAL NOT NULL,"
                "description TEXT, description_model TEXT, described_at REAL,"
                "PRIMARY KEY(scene, platform_id, image_index))"
            )
        elif version == 10:
            db.execute("ALTER TABLE model_calls ADD COLUMN mind_entry_seq INTEGER")
            db.execute("CREATE INDEX turn_calls ON model_calls(turn_id, id)")
        elif version == 11:
            db.execute("ALTER TABLE turns ADD COLUMN wake_received_at REAL")
            db.execute("ALTER TABLE turns ADD COLUMN first_expression_at REAL")
            db.execute("ALTER TABLE turns ADD COLUMN first_expression_delivery TEXT")
        elif version == 12:
            db.execute("ALTER TABLE model_calls ADD COLUMN cost TEXT")
        elif version == 13:
            db.execute(
                "CREATE TABLE tasks ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, requester TEXT NOT NULL,"
                "goal TEXT NOT NULL, deliverable TEXT NOT NULL, context TEXT NOT NULL,"
                "input TEXT NOT NULL,"
                "status TEXT NOT NULL CHECK(status IN "
                "('queued','running','waiting_input','done','failed','cancelled')),"
                "created REAL NOT NULL, started REAL, ended REAL,"
                "container TEXT, question TEXT, summary TEXT, error TEXT)"
            )
            db.execute("CREATE INDEX tasks_scene_status ON tasks(scene,status,id)")
            db.execute("CREATE INDEX tasks_status ON tasks(status,id)")
            db.execute("CREATE INDEX tasks_containers ON tasks(id) WHERE container IS NOT NULL")
            db.execute("CREATE INDEX tasks_requester_created ON tasks(scene,requester,created)")
            db.execute(
                "CREATE TABLE task_events ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, task_id INTEGER NOT NULL,"
                "kind TEXT NOT NULL, body TEXT NOT NULL, notice TEXT,"
                "created REAL NOT NULL, delivered_at REAL)"
            )
            db.execute("CREATE INDEX task_events_task ON task_events(scene,task_id,id)")
            db.execute(
                "CREATE INDEX task_events_pending_notice ON task_events(scene,id) "
                "WHERE notice IS NOT NULL AND delivered_at IS NULL"
            )
            db.execute(
                "CREATE TABLE task_files ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, task_id INTEGER NOT NULL,"
                "name TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,"
                "note TEXT, created REAL NOT NULL)"
            )
            db.execute("CREATE INDEX task_files_task ON task_files(scene,task_id,id)")
        elif version == 14:
            db.execute(
                "ALTER TABLE schedules ADD COLUMN interval_seconds INTEGER "
                "CHECK(interval_seconds BETWEEN 60 AND 31536000)"
            )
        elif version == 15:
            db.execute(
                "ALTER TABLE schedules ADD COLUMN cron_minute_of_day INTEGER "
                "CHECK(cron_minute_of_day IS NULL OR "
                "(cron_minute_of_day BETWEEN 0 AND 1439 AND interval_seconds IS NULL))"
            )
        elif version == 16:
            db.execute(
                "CREATE TABLE media ("
                "id INTEGER PRIMARY KEY, persona_id TEXT NOT NULL, file TEXT NOT NULL,"
                "mime_type TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,"
                "animated INTEGER NOT NULL, data BLOB NOT NULL)"
            )
            db.execute("CREATE INDEX media_persona_file ON media(persona_id,file,id)")
            db.execute(
                "CREATE TABLE message_media ("
                "message_seq INTEGER NOT NULL, image_index INTEGER NOT NULL,"
                "media_id INTEGER NOT NULL, description TEXT NOT NULL,"
                "emotions TEXT NOT NULL, tags TEXT NOT NULL,"
                "PRIMARY KEY(message_seq,image_index))"
            )
        elif version == 17:
            db.execute("CREATE TABLE learning_state (scene TEXT PRIMARY KEY, after_seq INTEGER NOT NULL)")
            db.execute(
                "CREATE TABLE learning_batches ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, after_seq INTEGER NOT NULL,"
                "through_seq INTEGER NOT NULL, started REAL NOT NULL, ended REAL, model_started REAL,"
                "status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),"
                "request TEXT NOT NULL, response TEXT, usage TEXT, cost TEXT, error TEXT)"
            )
            db.execute("CREATE INDEX learning_batches_scene ON learning_batches(scene,id)")
            db.execute(
                "CREATE TABLE expressions ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, situation TEXT NOT NULL,"
                "style TEXT NOT NULL, sources TEXT NOT NULL,"
                "status TEXT NOT NULL CHECK(status IN ('pending','adopted','rejected')),"
                "updated REAL NOT NULL, UNIQUE(scene,situation,style))"
            )
            db.execute("CREATE INDEX expressions_scene_status ON expressions(scene,status,id)")
        elif version == 18:
            db.execute("ALTER TABLE expressions ADD COLUMN vector BLOB")
            db.execute("ALTER TABLE expressions ADD COLUMN vector_binding TEXT")
            db.execute("ALTER TABLE expressions ADD COLUMN vector_dimensions INTEGER")
            db.execute(
                "CREATE TABLE expression_embedding_calls ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, turn_id TEXT,"
                "purpose TEXT NOT NULL CHECK(purpose IN ('query','index','reindex')),"
                "started REAL NOT NULL, ended REAL, request TEXT NOT NULL,"
                "response TEXT, usage TEXT, cost TEXT, error TEXT)"
            )
            db.execute("CREATE INDEX expression_embedding_scene ON expression_embedding_calls(scene,id)")
        db.execute(f"PRAGMA user_version = {version + 1}")
        db.commit()
    except BaseException:
        db.rollback()
        raise


def migrate_database(path: Path) -> Path:
    """Upgrade earlier formats while retaining a copy of each step."""
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, isolation_level=None)) as db:
        application_id, version = _format(db)
        if application_id != APPLICATION_ID or version not in range(1, FORMAT_VERSION):
            raise ValueError(
                f"Expected a next-core format 1 through {FORMAT_VERSION - 1} database: {path}; "
                f"found app={application_id}, version={version}"
            )
        for step in range(version, FORMAT_VERSION):
            backup = path.with_name(path.name + f".v{step}.bak")
            if backup.exists():
                raise FileExistsError(f"Migration backup already exists: {backup}")
        original_backup = path.with_name(path.name + f".v{version}.bak")
        for step in range(version, FORMAT_VERSION):
            _upgrade_one_step(db, path, step)
    return original_backup


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Migration takes no arguments; run from the configured instance directory")
    config = load_instance_config(Path.cwd())
    backup = migrate_database(config.database)
    print(f"Offline migration completed; input-format copy: {backup}")


if __name__ == "__main__":
    main()
