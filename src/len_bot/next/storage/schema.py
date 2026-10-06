"""Create the current database from domain-owned schemas in one transaction."""

import sqlite3


SCHEMA = """
CREATE TABLE messages (
    seq INTEGER PRIMARY KEY, scene TEXT NOT NULL,
    platform_id TEXT, body TEXT NOT NULL, raw TEXT,
    received_at REAL, persona_id TEXT
);
CREATE UNIQUE INDEX platform_messages ON messages(scene, platform_id)
    WHERE platform_id IS NOT NULL;
CREATE VIRTUAL TABLE message_search USING fts5(
    search_text, tokenize='trigram case_sensitive 1'
);
CREATE TABLE notices (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL, kind TEXT NOT NULL,
    platform_id TEXT, time REAL NOT NULL, received_at REAL NOT NULL, raw TEXT NOT NULL, body TEXT NOT NULL
);
CREATE INDEX scene_notices ON notices(scene,id);
CREATE INDEX recalled_messages ON notices(scene,platform_id)
    WHERE kind IN ('group_recall','friend_recall');
CREATE TABLE mind_entries (
    seq INTEGER PRIMARY KEY, scene TEXT NOT NULL,
    message TEXT NOT NULL, created REAL NOT NULL
);
CREATE INDEX scene_entries ON mind_entries(scene, seq);
CREATE TABLE mind_sessions (
    scene TEXT PRIMARY KEY,
    compact_through INTEGER NOT NULL DEFAULT 0,
    recap TEXT,
    last_message_seq INTEGER NOT NULL DEFAULT 0,
    attention_state TEXT,
    discovered_tools TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE turns (
    id TEXT PRIMARY KEY, scene TEXT NOT NULL, started REAL NOT NULL,
    ended REAL, status TEXT NOT NULL, error TEXT,
    wake_received_at REAL, first_expression_at REAL, first_expression_delivery TEXT
);
CREATE TABLE model_calls (
    id INTEGER PRIMARY KEY, turn_id TEXT, role TEXT NOT NULL,
    started REAL NOT NULL, ended REAL, request TEXT NOT NULL,
    response TEXT, usage TEXT, error TEXT, mind_entry_seq INTEGER, tokens TEXT,
    scene TEXT NOT NULL, plugin TEXT
);
CREATE INDEX turn_calls ON model_calls(turn_id, id);
CREATE TABLE web_documents (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL, body TEXT NOT NULL
);
CREATE TABLE image_cache (
    scene TEXT NOT NULL, platform_id TEXT NOT NULL,
    image_index INTEGER NOT NULL, jpeg BLOB NOT NULL,
    width INTEGER NOT NULL, height INTEGER NOT NULL,
    animated INTEGER NOT NULL, fetched_at REAL NOT NULL,
    description TEXT, description_model TEXT, described_at REAL,
    PRIMARY KEY(scene, platform_id, image_index)
);
CREATE TABLE media (
    id INTEGER PRIMARY KEY, persona_id TEXT, file TEXT,
    source_message_seq INTEGER, source_image_index INTEGER,
    mime_type TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    animated INTEGER NOT NULL, data BLOB NOT NULL,
    CHECK ((persona_id IS NOT NULL AND file IS NOT NULL AND
            source_message_seq IS NULL AND source_image_index IS NULL) OR
           (persona_id IS NULL AND file IS NULL AND
            source_message_seq IS NOT NULL AND source_image_index IS NOT NULL)),
    UNIQUE(source_message_seq,source_image_index)
);
CREATE INDEX media_persona_file ON media(persona_id,file,id);
CREATE TABLE message_media (
    message_seq INTEGER NOT NULL, image_index INTEGER NOT NULL,
    media_id INTEGER NOT NULL, description TEXT NOT NULL,
    emotions TEXT NOT NULL, tags TEXT NOT NULL,
    PRIMARY KEY(message_seq,image_index)
);
CREATE INDEX message_media_media ON message_media(media_id,message_seq);
CREATE INDEX message_send_window ON messages(scene,json_extract(body,'$.time'))
    WHERE json_extract(body,'$.is_self')=1;
CREATE INDEX message_retention ON messages(scene,COALESCE(received_at,json_extract(body,'$.time')),seq);
CREATE INDEX model_call_usage ON model_calls(started,turn_id);
CREATE INDEX model_call_expiry ON model_calls(ended,id);
CREATE INDEX turns_scene_time ON turns(scene,started);
CREATE INDEX turns_expiry ON turns(ended,id);
"""


def create_database(db: sqlite3.Connection, version: int) -> None:
    from ..media.audio_store import SCHEMA as AUDIO
    from ..learning.jargon_store import SCHEMA as JARGON
    from ..learning.store import SCHEMA as LEARNING
    from ..plugins.store import SCHEMA as PLUGINS
    from ..chat.proactive import SCHEMA as PROACTIVE
    from ..learning.reply_effect_store import SCHEMA as REPLY_EFFECTS
    from ..chat.schedule_store import SCHEMA as SCHEDULES
    from ..learning.sticker_store import SCHEMA as STICKERS
    from ..work.store import SCHEMA as TASKS

    domains = (SCHEMA, SCHEDULES, TASKS, AUDIO, LEARNING, JARGON, STICKERS,
               REPLY_EFFECTS, PROACTIVE, PLUGINS)
    db.executescript(f"BEGIN; PRAGMA application_id = 1279413809; PRAGMA user_version = {version};\n"
                     + "\n".join(domains) + "\nCOMMIT;")
