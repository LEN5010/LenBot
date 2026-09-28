"""Offline memory data migrations: processing database format and legacy ledger import."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

import pytest

from len_bot.next.config import load_host_config
from len_bot.next.import_legacy_memory import import_legacy_memory
from len_bot.next.memory_jobs import FORMAT_VERSION, MemoryJobs
from len_bot.next.messages import ChatMessage, Sender, Segment
from len_bot.next.migrate_memory_jobs import migrate_memory_jobs
from len_bot.next.store import Store


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
            INSERT INTO memory_cursors VALUES('group:80001', 12, 1790000000.0);
            INSERT INTO memory_jobs VALUES(3,'group:80001','local',5,12,'complete',1790000001.0,1790000002.0,
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
        path.with_name(path.name + ".v1.bak").unlink()
        path.with_name(path.name + ".v2.bak").unlink()
        path.with_name(path.name + ".v3.bak").unlink()
        with sqlite3.connect(path) as db:
            db.execute("DROP TABLE memory_embedding_calls")
            db.execute("DROP TABLE memory_summary_runs")
            db.execute("PRAGMA user_version=2")
            db.execute("INSERT INTO memory_exclusions VALUES('group:80001', 7)")
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
            [("group:80001", 7)] if start == 2 else [])
    assert _rows(path) == before == _rows(backup)
    with MemoryJobs(path) as jobs:
        assert jobs.after("group:80001") == 12
        run = jobs.begin_summary("group:80001", "group:80001", "people", {"messages": []})
        jobs.recover_summaries()
        assert jobs.summary_runs("group:80001", "people")[0]["status"] == "interrupted"
        assert jobs.summary_run(run)["request"] == {"messages": []}


def test_memory_processing_collision_rolls_back_and_backup_is_not_overwritten(tmp_path):
    path = tmp_path / "state.db.memory.sqlite3"
    _format1(path)
    migrate_memory_jobs(path)  # 1 -> 3 leaves v1 and v2 copies
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(FileExistsError, match="v2.bak"):
        migrate_memory_jobs(path)
    path.with_name(path.name + ".v2.bak").unlink()
    path.with_name(path.name + ".v3.bak").unlink()
    with pytest.raises(sqlite3.OperationalError, match="already exists"):
        migrate_memory_jobs(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def _legacy_source(path: Path) -> None:
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE memories (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, subject TEXT NOT NULL, kind TEXT NOT NULL,
                statement TEXT NOT NULL, basis TEXT NOT NULL, evidence TEXT NOT NULL, status TEXT NOT NULL,
                expires_at REAL, created_at REAL NOT NULL, revision INTEGER NOT NULL,
                created_event_id TEXT, revision_event_id TEXT, supersedes_ids TEXT NOT NULL,
                superseded_by TEXT, revision_reason TEXT NOT NULL, revision_evidence TEXT NOT NULL);
            CREATE TABLE open_loops (id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, target_actor_id TEXT NOT NULL,
                intent TEXT NOT NULL, source_event_id TEXT NOT NULL, status TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL, source_stimulus_id TEXT);
            INSERT INTO open_loops VALUES('loop1','group:80001','user:70001','合成待办','evt-1','active',1,2,NULL);
        """)
        rows = [
            ("mem_a", "group:80001", "user:70001", "preference", "脱敏甲喜欢合成话题", "reported",
             '["evt-1","evt-missing"]', "active", None, 1790000000.0, 2),
            ("mem_b", "group:80001", "group:80001", "group_norm", "本群合成惯例", "inferred", "[]",
             "active", None, 1790000100.0, 1),
            ("mem_c", "group:80001", "user:70001", "fact", "被更正的旧说法", "reported", "[]",
             "superseded", None, 1789000000.0, 1),
            ("mem_d", "group:80001", "user:70002", "fact", "已过期的说法", "reported", "[]",
             "active", 1.0, 1789000000.0, 1),
            ("mem_e", "group:89999", "user:70001", "fact", "未配置群的说法", "reported", "[]",
             "active", None, 1790000200.0, 1),
            ("mem_f", "group:80001", "bot", "fact", "主体不明", "reported", "[]",
             "active", None, 1790000300.0, 1),
        ]
        db.executemany("INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,'[]',NULL,'','[]')", rows)


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "host"
    role = root / "role"
    role.mkdir(parents=True)
    (role / "persona.yaml").write_text(json.dumps(dict(
        id="fixture", name="合成角色", brief="迁移", behavior="正常", self_reference=["我"], aliases=[],
        tools=["say"], skills=[], styles=[]), ensure_ascii=False))
    for name, content in (("voice.md", "简短"), ("boundaries.md", "合成"), ("examples.yaml", "[]")):
        (role / name).write_text(content)
    _legacy_source(root / "legacy.sqlite3")
    source = dict(mode="isolated-multi", bot_qq="90001", timezone="Asia/Shanghai", database="state.db",
                  delivery="simulated",
                  onebot={"mode": "forward_ws", "ws_url": "ws://127.0.0.1:9/unused", "access_token": "synthetic"},
                  models={"providers": {"local": {"api": "openai-chat", "base_url": "http://127.0.0.1:9/v1",
                                                  "api_key": "synthetic"}},
                          "roles": {name: {"provider": "local", "model": name, "context_window_tokens": 16384}
                                    for name in ("mind", "voice")}},
                  memory={"backend": "local", "local": {"directory": "memory"}},
                  history_import={"source": "legacy.sqlite3", "backup": "backup.sqlite3", "scenes": ["group:80001"]},
                  scenes={"group:80001": {"persona": "role"}})
    (root / "lenbot.config.json").write_text(json.dumps(source, ensure_ascii=False))
    config = load_host_config(root)
    with Store(config.database) as store:
        message = ChatMessage(id="m1", platform="qq", scene="group:80001", platform_message_id="5001",
                              sender=Sender("70001", "脱敏甲", None, "member"), time=1790000000.0,
                              segments=[Segment("text", {"text": "合成原话"})], reply_to=None,
                              mentions_bot=False, is_self=False, send_status="received")
        with store.db:
            store._save_message(message, {"legacy_events": [{"rowid": 1, "event": {"id": "evt-1"}}]}, received_at=None)
        assert store.db.execute("SELECT seq FROM messages").fetchone()[0] == 1
    return root


def test_legacy_memories_become_pending_scene_files_and_report(tmp_path):
    root = _root(tmp_path)
    config = load_host_config(root)
    result = asyncio.run(import_legacy_memory(config))
    base = root / "memory" / "groups" / "80001" / "legacy-import"
    person = (base / "people" / "70001.md").read_text()
    group = (base / "group.md").read_text()
    assert person.startswith("# 旧核心迁移记忆（待确认）")
    assert "[preference · reported] 脱敏甲喜欢合成话题" in person and "旧记录 mem_a，第 2 次修订" in person
    assert "本场景原话 record 1" in person and "未导入的旧事件 evt-missing" in person
    assert "被更正的旧说法" not in person and "[group_norm · inferred] 本群合成惯例" in group
    assert not (base / "people" / "70002.md").exists()
    report = json.loads(Path(result["report"]).read_text())
    assert report["skipped"] == {"superseded": 1, "expired": 1}
    assert sorted((item["id"], item["reason"]) for item in report["quarantined"]) == [
        ("mem_e", "scope 不是本次迁移的已配置场景"), ("mem_f", "subject 不是 user:<QQ> 或本场景")]
    assert report["not_migrated"] == {"memory_index": None, "public_interests_all_scenes": None, "open_loops": 1}
    assert [item["path"] for item in report["written"]] == ["legacy-import/group.md", "legacy-import/people/70001.md"]
    with sqlite3.connect(root / "memory" / ".memory-index.sqlite3") as db:
        assert db.execute("SELECT reason FROM memory_changes").fetchall() == [("旧核心记忆离线迁移（待确认）",)] * 2

    with pytest.raises(FileExistsError, match="report already exists"):
        asyncio.run(import_legacy_memory(config))
    Path(result["report"]).unlink()
    with pytest.raises(FileExistsError, match="legacy-import/ already exists"):
        asyncio.run(import_legacy_memory(config))
    assert not Path(result["report"]).exists()


def test_legacy_memory_bad_evidence_stops_before_any_write(tmp_path):
    root = _root(tmp_path)
    with sqlite3.connect(root / "legacy.sqlite3") as db:
        db.execute("UPDATE memories SET evidence='not json' WHERE id='mem_a'")
    with pytest.raises(ValueError, match="mem_a.*evidence"):
        asyncio.run(import_legacy_memory(load_host_config(root)))
    assert not (root / "memory" / "groups").exists()
    assert not (root / "state.db.legacy-memory.json").exists()
