"""Explicit offline upgrade of an isolated next-core database to the current format."""

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
        elif version == 19:
            db.execute(
                "CREATE TABLE jargon_state ("
                "scene TEXT PRIMARY KEY, start_seq INTEGER NOT NULL, after_seq INTEGER NOT NULL)"
            )
            db.execute(
                "CREATE TABLE jargon ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, term TEXT NOT NULL,"
                "count INTEGER NOT NULL, sample_seqs TEXT NOT NULL,"
                "latest_meaning TEXT, confidence REAL, meaning TEXT,"
                "last_inference_count INTEGER NOT NULL DEFAULT 0,"
                "status TEXT NOT NULL CHECK(status IN ('pending','adopted','rejected')),"
                "updated REAL NOT NULL, UNIQUE(scene,term))"
            )
            db.execute("CREATE INDEX jargon_scene_status ON jargon(scene,status,id)")
            db.execute(
                "CREATE TABLE jargon_calls ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,"
                "purpose TEXT NOT NULL CHECK(purpose IN ('discovery','meaning')),"
                "after_seq INTEGER, through_seq INTEGER, term_id INTEGER, inference_count INTEGER,"
                "started REAL NOT NULL, ended REAL,"
                "status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),"
                "model_started REAL, request TEXT NOT NULL,"
                "response TEXT, usage TEXT, cost TEXT, error TEXT)"
            )
            db.execute("CREATE INDEX jargon_calls_scene ON jargon_calls(scene,id)")
        elif version == 20:
            current_max = db.execute("SELECT COALESCE(MAX(id),0) FROM expressions").fetchone()[0]
            referenced_max = db.execute(
                "SELECT COALESCE(MAX(j.value),0) FROM model_calls AS c, "
                "json_each(c.request,'$.expression_ids') AS j WHERE c.role='voice'"
            ).fetchone()[0]
            db.execute(
                "CREATE TABLE expressions_next ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, situation TEXT NOT NULL,"
                "style TEXT NOT NULL, sources TEXT NOT NULL,"
                "status TEXT NOT NULL CHECK(status IN ('pending','adopted','rejected')),"
                "updated REAL NOT NULL, vector BLOB, vector_binding TEXT,"
                "vector_dimensions INTEGER, UNIQUE(scene,situation,style))"
            )
            db.execute(
                "INSERT INTO expressions_next(id,scene,situation,style,sources,status,updated,"
                "vector,vector_binding,vector_dimensions) "
                "SELECT id,scene,situation,style,sources,status,updated,"
                "vector,vector_binding,vector_dimensions FROM expressions"
            )
            db.execute("DROP TABLE expressions")
            db.execute("ALTER TABLE expressions_next RENAME TO expressions")
            db.execute("CREATE INDEX expressions_scene_status ON expressions(scene,status,id)")
            db.execute("DELETE FROM sqlite_sequence WHERE name='expressions'")
            db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('expressions',?)",
                       (max(current_max, referenced_max),))
        elif version == 21:
            db.execute(
                "CREATE TABLE media_next ("
                "id INTEGER PRIMARY KEY, persona_id TEXT, file TEXT,"
                "source_message_seq INTEGER, source_image_index INTEGER,"
                "mime_type TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,"
                "animated INTEGER NOT NULL, data BLOB NOT NULL,"
                "CHECK ((persona_id IS NOT NULL AND file IS NOT NULL AND "
                "source_message_seq IS NULL AND source_image_index IS NULL) OR "
                "(persona_id IS NULL AND file IS NULL AND "
                "source_message_seq IS NOT NULL AND source_image_index IS NOT NULL)),"
                "UNIQUE(source_message_seq,source_image_index))"
            )
            db.execute(
                "INSERT INTO media_next(id,persona_id,file,mime_type,width,height,animated,data) "
                "SELECT id,persona_id,file,mime_type,width,height,animated,data FROM media"
            )
            db.execute("DROP TABLE media")
            db.execute("ALTER TABLE media_next RENAME TO media")
            db.execute("CREATE INDEX media_persona_file ON media(persona_id,file,id)")
            db.execute("CREATE INDEX message_media_media ON message_media(media_id,message_seq)")
            db.execute(
                "CREATE TABLE sticker_candidates ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,"
                "source_message_seq INTEGER NOT NULL, image_index INTEGER NOT NULL,"
                "media_id INTEGER, status TEXT NOT NULL CHECK(status IN "
                "('queued','running','complete','failed','interrupted')),"
                "review TEXT NOT NULL CHECK(review IN ('pending','adopted','rejected')),"
                "description TEXT, text TEXT, emotions TEXT NOT NULL DEFAULT '[]',"
                "tags TEXT NOT NULL DEFAULT '[]', is_sticker INTEGER,"
                "created REAL NOT NULL, updated REAL NOT NULL, error TEXT,"
                "UNIQUE(scene,source_message_seq,image_index))"
            )
            db.execute("CREATE INDEX sticker_candidates_status ON sticker_candidates(scene,status,review,id)")
            db.execute("CREATE INDEX sticker_candidates_review ON sticker_candidates(scene,review,id)")
            db.execute(
                "CREATE TABLE sticker_calls ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,"
                "candidate_id INTEGER NOT NULL, source_message_seq INTEGER NOT NULL,"
                "image_index INTEGER NOT NULL, started REAL NOT NULL, ended REAL,"
                "status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),"
                "model_started REAL, request TEXT, response TEXT, usage TEXT, cost TEXT, error TEXT)"
            )
            db.execute("CREATE INDEX sticker_calls_scene ON sticker_calls(scene,id)")
        elif version == 22:
            db.execute(
                "CREATE TABLE reply_effects ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,"
                "entry_seq INTEGER NOT NULL UNIQUE, turn_id TEXT, channels TEXT NOT NULL,"
                "message_seqs TEXT NOT NULL, planned_parts INTEGER NOT NULL,"
                "first_sent_at REAL NOT NULL, last_sent_at REAL NOT NULL, deadline REAL NOT NULL,"
                "observed_seqs TEXT, closed_at REAL, input_gap INTEGER, call_id INTEGER,"
                "reaction TEXT CHECK(reaction IS NULL OR reaction IN "
                "('agree','continue','correct','negative','unrelated','uncertain')),"
                "reason TEXT)"
            )
            db.execute("CREATE INDEX reply_effects_scene ON reply_effects(scene,id)")
            db.execute("CREATE INDEX reply_effects_open ON reply_effects(scene,id) WHERE closed_at IS NULL")
            db.execute(
                "CREATE TABLE reply_effect_calls ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,"
                "effect_ids TEXT NOT NULL, started REAL NOT NULL, ended REAL,"
                "status TEXT NOT NULL CHECK(status IN ('running','complete','failed','interrupted')),"
                "model_started REAL, request TEXT NOT NULL,"
                "response TEXT, usage TEXT, cost TEXT, error TEXT)"
            )
            db.execute("CREATE INDEX reply_effect_calls_scene ON reply_effect_calls(scene,id)")
        elif version == 23:
            db.execute(
                "CREATE TABLE schedules_next ("
                "id INTEGER PRIMARY KEY, scene TEXT NOT NULL,"
                "created REAL NOT NULL, due_at REAL NOT NULL,"
                "timezone TEXT NOT NULL, note TEXT NOT NULL,"
                "target TEXT NOT NULL, requester TEXT,"
                "status TEXT NOT NULL, delivered_at REAL, reason TEXT,"
                "interval_seconds INTEGER CHECK(interval_seconds BETWEEN 60 AND 31536000),"
                "cron TEXT CHECK(cron IS NULL OR interval_seconds IS NULL))"
            )
            db.execute(
                "INSERT INTO schedules_next(id,scene,created,due_at,timezone,note,target,requester,"
                "status,delivered_at,reason,interval_seconds,cron) "
                "SELECT id,scene,created,due_at,timezone,note,target,requester,"
                "status,delivered_at,reason,interval_seconds,"
                "CASE WHEN cron_minute_of_day IS NULL THEN NULL ELSE "
                "'cron:' || (cron_minute_of_day % 60) || ' ' || (cron_minute_of_day / 60) || ' * * *' END "
                "FROM schedules"
            )
            db.execute("DROP TABLE schedules")
            db.execute("ALTER TABLE schedules_next RENAME TO schedules")
            db.execute("CREATE INDEX schedules_status_due ON schedules(scene,status,due_at,id)")
            db.execute(
                "CREATE TABLE proactive_wakes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL,"
                "turn_id TEXT NOT NULL UNIQUE, woke_at REAL NOT NULL,"
                "local_date TEXT NOT NULL, idle_since REAL NOT NULL,"
                "outcome TEXT CHECK(outcome IS NULL OR outcome IN ('silent','answered','ignored','unobserved')),"
                "closed_at REAL, UNIQUE(scene,local_date))"
            )
        elif version == 24:
            db.execute(
                "CREATE TABLE plugin_events ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, scene TEXT NOT NULL, plugin TEXT NOT NULL,"
                "kind TEXT NOT NULL CHECK(kind IN ('event','reply')),"
                "content TEXT NOT NULL, created REAL NOT NULL, delivered_at REAL)"
            )
            db.execute("CREATE INDEX plugin_events_pending ON plugin_events(scene,id) WHERE delivered_at IS NULL")
        elif version == 25:
            db.execute("ALTER TABLE proactive_wakes ADD COLUMN assessment TEXT NOT NULL DEFAULT 'arrival_count' "
                       "CHECK(assessment IN ('arrival_count','reply_effects'))")
            db.execute("CREATE INDEX reply_effects_turn ON reply_effects(scene,turn_id)")
        elif version == 26:
            db.execute("CREATE TABLE audio_cache ("
                       "scene TEXT NOT NULL, platform_id TEXT NOT NULL, audio_index INTEGER NOT NULL,"
                       "wav BLOB NOT NULL, duration REAL NOT NULL, fetched_at REAL NOT NULL,"
                       "transcript TEXT, provider TEXT, model TEXT, transcribed_at REAL,"
                       "PRIMARY KEY(scene,platform_id,audio_index))")
        elif version == 27:
            db.execute("CREATE TABLE audio_cache_next ("
                       "scene TEXT NOT NULL, platform_id TEXT NOT NULL, audio_index INTEGER NOT NULL,"
                       "wav BLOB, duration REAL, fetched_at REAL,"
                       "transcript TEXT, provider TEXT, model TEXT, transcribed_at REAL,"
                       "status TEXT NOT NULL CHECK(status IN ('idle','queued','running','complete','failed','interrupted')),"
                       "created REAL NOT NULL, updated REAL NOT NULL, error TEXT, announced_at REAL,"
                       "PRIMARY KEY(scene,platform_id,audio_index))")
            db.execute("INSERT INTO audio_cache_next SELECT *,"
                       "CASE WHEN transcript IS NULL THEN 'idle' ELSE 'complete' END,"
                       "fetched_at,COALESCE(transcribed_at,fetched_at),NULL,transcribed_at FROM audio_cache")
            db.execute("DROP TABLE audio_cache")
            db.execute("ALTER TABLE audio_cache_next RENAME TO audio_cache")
            db.execute("CREATE INDEX audio_queued ON audio_cache(scene,created) WHERE status='queued'")
            db.execute("CREATE INDEX audio_results ON audio_cache(scene,transcribed_at) "
                       "WHERE announced_at IS NULL AND transcript IS NOT NULL")
            db.execute("CREATE TABLE audio_calls ("
                       "id INTEGER PRIMARY KEY, scene TEXT NOT NULL, platform_id TEXT NOT NULL,"
                       "audio_index INTEGER NOT NULL, started REAL NOT NULL, ended REAL,"
                       "request TEXT NOT NULL, response TEXT, usage TEXT, error TEXT)")
            db.execute("CREATE INDEX audio_calls_source ON audio_calls(scene,platform_id,audio_index,id)")
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
