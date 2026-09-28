"""Explicit offline import of legacy ledger memories as scene-local material awaiting confirmation."""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import closing
from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

from .config import HostConfig, LabConfig, load_instance_config
from .memory import LEGACY_IMPORT, LocalMemoryConfig, open_memory
from .store import Store, encode
from .model_slots import ModelSlots
from .limits import ModelBudget


COLUMNS = ("id", "scope", "subject", "kind", "statement", "basis", "evidence", "status",
           "expires_at", "created_at", "revision")
REASON = "旧核心记忆离线迁移（待确认）"
HEADER = ("# 旧核心迁移记忆（待确认）\n\n"
          "以下条目由离线迁移从旧核心记忆账本导入，尚未经运营者确认，不进入每轮自动召回。"
          "确认后请改写到正式人物或本群文件，再删除本文件。\n\n")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant: {value}")


def _evidence(row: sqlite3.Row) -> list[str]:
    try:
        value = json.loads(row["evidence"], parse_constant=_reject_constant)
        if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
            raise ValueError("evidence must be a JSON array of event ids")
        return value
    except (TypeError, ValueError) as error:
        raise ValueError(f"Invalid legacy memory {row['id']!r} evidence: {error}; raw={row['evidence']!r:.300}") from error


def _event_records(store: Store, scene: str) -> dict[str, int]:
    """Map legacy event ids to messages already imported into this scene."""
    records: dict[str, int] = {}
    for seq, raw in store.db.execute(
            "SELECT seq,raw FROM messages WHERE scene=? AND raw IS NOT NULL "
            "AND json_extract(raw,'$.legacy_events') IS NOT NULL", (scene,)):
        for item in json.loads(raw)["legacy_events"]:
            records[item["event"]["id"]] = seq
    return records


def _count(db: sqlite3.Connection, table: str, scenes: list[str] | None = None, column: str | None = None) -> int | None:
    """Rows left behind by this step; None when the legacy table does not exist."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
        return None
    if scenes is None:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    placeholders = ",".join("?" for _ in scenes)
    return db.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} IN ({placeholders})", scenes).fetchone()[0]


def _render(entries: list[tuple[sqlite3.Row, list[int], list[str]]], subject: str, timezone: str) -> str:
    lines = [HEADER + f"主体：{subject}\n"]
    zone = ZoneInfo(timezone)
    for row, records, missing in entries:
        created = datetime.fromtimestamp(row["created_at"], zone).isoformat(sep=" ", timespec="seconds")
        lines.append(f"- [{row['kind']} · {row['basis']}] {row['statement']}")
        lines.append(f"  - 旧记录 {row['id']}，第 {row['revision']} 次修订，记录于 {created}")
        sources = []
        if records:
            sources.append("本场景原话 record " + "、".join(map(str, records)))
        if missing:
            sources.append("未导入的旧事件 " + "、".join(missing))
        lines.append("  - 证据：" + ("；".join(sources) if sources else "旧记录没有证据"))
    return "\n".join(lines) + "\n"


def plan_legacy_memory(config: LabConfig | HostConfig, store: Store, now: float) -> tuple[dict, dict]:
    """Read the legacy ledger once and decide every row; returns files per scene and the report."""
    settings = config.history_import
    if settings is None:
        raise ValueError("history_import must be explicitly configured; its source is the legacy database")
    if not isinstance(config.memory, LocalMemoryConfig):
        raise ValueError("Legacy memory import writes local memory files; configure memory.backend=local")
    source = settings.source
    if not source.is_file():
        raise ValueError(f"Legacy source is not an offline SQLite file: {source}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(source) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"Legacy source has a nonempty {suffix} file; use a complete offline snapshot: {source}")
    scenes = list(settings.scenes)
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as legacy:
        legacy.row_factory = sqlite3.Row
        columns = {row[1] for row in legacy.execute("PRAGMA table_info(memories)")}
        if not set(COLUMNS) <= columns:
            raise ValueError(f"Legacy memories schema lacks {sorted(set(COLUMNS) - columns)}: {source}")
        rows = legacy.execute(f"SELECT {','.join(COLUMNS)} FROM memories ORDER BY created_at,id").fetchall()
        not_migrated = {"memory_index": _count(legacy, "memory_index", scenes, "scope"),
                        # public_interests has no scene column; this is the whole table.
                        "public_interests_all_scenes": _count(legacy, "public_interests"),
                        "open_loops": _count(legacy, "open_loops", scenes, "scene_id")}
    records = {scene: _event_records(store, scene) for scene in scenes}
    planned: dict[str, dict[str, list]] = {scene: {} for scene in scenes}
    skipped: Counter[str] = Counter()
    quarantined = []
    for row in rows:
        if row["status"] != "active":
            skipped[row["status"]] += 1
            continue
        if row["expires_at"] is not None and row["expires_at"] <= now:
            skipped["expired"] += 1
            continue
        scene, subject = row["scope"], row["subject"]
        person = re.fullmatch(r"user:([1-9][0-9]*)", subject)
        if scene not in planned:
            quarantined.append({**dict(row), "reason": "scope 不是本次迁移的已配置场景"})
            continue
        if subject == scene:
            path, label = f"{LEGACY_IMPORT}/group.md", "本场景"
        elif person is not None:
            path, label = f"{LEGACY_IMPORT}/people/{person.group(1)}.md", f"QQ {person.group(1)}"
        else:
            quarantined.append({**dict(row), "reason": "subject 不是 user:<QQ> 或本场景"})
            continue
        evidence = _evidence(row)
        found = [records[scene][event] for event in evidence if event in records[scene]]
        missing = [event for event in evidence if event not in records[scene]]
        planned[scene].setdefault(path, [label, []])[1].append((row, found, missing))
    files = {scene: {path: _render(entries, label, config.scene_timezone(scene))
                     for path, (label, entries) in paths.items()}
             for scene, paths in planned.items()}
    report = {"source": str(source), "planned_at": now,
              "scenes": {scene: {path: len(entries) for path, (_, entries) in paths.items()}
                         for scene, paths in planned.items()},
              "skipped": dict(skipped), "quarantined": quarantined, "not_migrated": not_migrated}
    return files, report


async def import_legacy_memory(config: LabConfig | HostConfig) -> dict:
    report_path = config.database.with_name(config.database.name + ".legacy-memory.json")
    if report_path.exists():
        raise FileExistsError(f"Legacy memory report already exists: {report_path}")
    root = config.memory.local.directory.expanduser().resolve() if isinstance(config.memory, LocalMemoryConfig) else None
    with Store(config.database) as store:
        files, report = plan_legacy_memory(config, store, time.time())
    existing = [scene for scene in files if root is not None and (
        root / ("groups" if scene.startswith("group:") else "private") / scene.split(":", 1)[1] / LEGACY_IMPORT).exists()]
    if existing:
        raise FileExistsError(f"{LEGACY_IMPORT}/ already exists in memory partitions {existing}; nothing was written")
    written: list[dict] = []
    try:
        with Store(config.database) as store:
            slots = ModelSlots(config.max_model_requests)
            budget = ModelBudget(config, store, None)
            slots.admit = budget.check
            async with open_memory(config, store, slots=slots) as memory:
                budget.memory = memory
                for scene, paths in files.items():
                    for path, content in sorted(paths.items()):
                        change = await memory.backend.write(scene, path, content, REASON)
                        written.append({"scene": scene, "path": path, "chars": len(change.after)})
    except BaseException as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        report["written"] = written
        with report_path.open("x", encoding="utf-8") as stream:
            stream.write(encode(report))
    return {"report": str(report_path), "written": written, "skipped": report["skipped"],
            "quarantined": len(report["quarantined"]), "not_migrated": report["not_migrated"]}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Legacy memory import takes no arguments; stop the instance and run from its root")
    print(encode(asyncio.run(import_legacy_memory(load_instance_config(Path.cwd())))))


if __name__ == "__main__":
    main()
