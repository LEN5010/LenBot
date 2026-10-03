"""Run structured development cases through the unchanged isolated lab entry point."""

from __future__ import annotations

import argparse
import asyncio
import filecmp
import json
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .cases import CaseFile, InitialMemory, ReplayCase, load_cases
from .memory_snapshot import MemoryBaseline, check_memory, freeze_memory, install_memory, observed_memory, same_memory
from ..next.chat.context import PROMPTS
from ..next.chat.tools import build_tools
from ..next.config import LabConfig, load_config
from ..next.memory.service import LocalMemoryConfig, OpenVikingMemoryConfig
from ..next.trials.replay_web import RecordedWeb
from ..next.trials.replay_images import RecordedImages
from ..next.trials.replay_memory import MemoryConsumption, RecordedMemory
from ..next.persona.profile import Persona, load_persona
from ..next.models.pricing import cost_summary
from ..next.storage.store import FORMAT_VERSION, encode, turn_record


LOCAL_TOOLS = {"say", "wait", "recall_chat", "schedule", "schedule_list", "schedule_cancel",
               "persona_knowledge", "tool_search", "react", "scene_control", "memory", "web_read", "web_search", "look"}


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    case_id: str
    repeat: int = Field(gt=0, strict=True)
    verdict: Literal["unreviewed", "pass", "fail", "uncertain"]
    reason: str

    @model_validator(mode="after")
    def reviewed_reason(self) -> Annotation:
        if self.verdict != "unreviewed" and not self.reason.strip():
            raise ValueError("Reviewed annotation requires a reason")
        return self


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")


def check_initial_database(path: Path, config: LabConfig) -> None:
    if not path.is_file():
        raise ValueError(f"Initial database is not an existing file: {path}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"Initial database must be offline without nonempty {suffix}: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        if (db.execute("PRAGMA application_id").fetchone()[0] != 0x4C424E31
                or db.execute("PRAGMA user_version").fetchone()[0] != FORMAT_VERSION):
            raise ValueError(
                f"Initial database requires current next-core format {FORMAT_VERSION}; "
                f"no automatic migration: {path}"
            )
        scenes = {row[0] for row in db.execute(" UNION ".join(
            f"SELECT scene FROM {table}" for table in (
                "messages", "mind_entries", "mind_sessions", "turns", "schedules", "web_documents",
                "notices", "image_cache", "audio_cache", "audio_calls", "tasks", "task_events", "task_files", "learning_state",
                "learning_batches", "expressions", "expression_embedding_calls",
                "jargon_state", "jargon", "jargon_calls", "sticker_candidates", "sticker_calls",
                "reply_effects", "reply_effect_calls", "proactive_wakes", "plugin_events",
            )
        ))}
        if scenes - {config.scene}:
            raise ValueError(f"Initial database contains scenes other than {config.scene}: {sorted(scenes)!r}; {path}")
        mismatch = db.execute(
            "SELECT seq FROM messages WHERE "
            "(json_extract(body,'$.is_self')=1 AND json_extract(body,'$.sender.uid')!=?) OR "
            "(json_extract(raw,'$.self_id') IS NOT NULL AND CAST(json_extract(raw,'$.self_id') AS TEXT)!=?) LIMIT 1",
            (config.bot_qq, config.bot_qq),
        ).fetchone()
        if mismatch is not None:
            raise ValueError(f"Initial database message {mismatch[0]} has a different Bot QQ from {config.bot_qq}: {path}")
        previous = db.execute(
            "SELECT request FROM model_calls JOIN turns ON turns.id=model_calls.turn_id "
            "WHERE turns.scene=? AND role='mind' ORDER BY model_calls.id DESC LIMIT 1", (config.scene,),
        ).fetchone()
        if previous is not None:
            settings = json.loads(previous[0])["settings"]
            current = config.model_settings("mind")
            if any(settings[key] != getattr(current, key) for key in ("api", "base_url", "model")):
                raise ValueError(f"Initial database mind binding differs; native history cannot switch models: {path}")


def prepare(root: Path, set_name: str, profile: str) -> tuple[LabConfig, Persona, CaseFile, dict]:
    config = load_config(root)
    if (config.replay_clock is not None or config.replay_web is not None
            or config.replay_images is not None or config.replay_memory is not None):
        raise ValueError("Evaluation template root must not contain replay runtime anchors; each repeat creates its own archive")
    if config.evaluation is None:
        raise ValueError("Root configuration has no evaluation settings")
    if set_name not in config.evaluation.sets or profile not in config.evaluation.profiles:
        raise ValueError(f"Unknown configured evaluation set/profile: {set_name!r}/{profile!r}")
    if config.onebot is not None or config.delivery != "simulated" or config.panel is not None:
        raise ValueError("Development replay requires onebot=null, delivery=simulated and panel=null")
    if isinstance(config.memory, OpenVikingMemoryConfig) and config.memory.ingest is not None:
        raise ValueError('Frozen native memory replay does not implement asynchronous ingest timing; disable ingest explicitly')
    persona = load_persona(config.persona)
    if persona.tools != "all" and (unsupported := set(persona.tools) - LOCAL_TOOLS):
        raise ValueError(f"Development replay does not implement these declared tools: {sorted(unsupported)}")
    unsupported = {tool["function"]["name"] for tool in build_tools(config, persona, platform=False)} - LOCAL_TOOLS
    if unsupported:
        raise ValueError(f"Development replay does not implement these effective tools: {sorted(unsupported)}")
    cases = load_cases(config.evaluation.sets[set_name], set_name=set_name,
                       scene=config.scene, bot_qq=config.bot_qq)
    for case in cases.cases:
        if isinstance(config.memory, OpenVikingMemoryConfig):
            if case.memory_materials is None:
                raise ValueError(f'Case {case.id}: native memory requires explicit memory_materials; live service is not used')
            if case.initial_memory is not None:
                raise ValueError(f'Case {case.id}: initial_memory is a local backend seed, not a native service snapshot')
            RecordedMemory(case.memory_materials).check_settings(config.memory.openviking)
        elif case.memory_materials is not None:
            raise ValueError(f'Case {case.id}: memory_materials requires the configured openviking backend')
        if config.models.roles.vision is not None and case.image_materials is None:
            raise ValueError(f"Case {case.id}: vision requires explicit image_materials; live image downloads are not used")
        if case.image_materials is not None:
            RecordedImages(case.image_materials, max_bytes=config.images.max_bytes)
        if (config.web_read is not None or config.web_search is not None) and case.web_materials is None:
            raise ValueError(f"Case {case.id}: web tools require explicit web_materials; live network is not used")
        if case.web_materials is not None:
            RecordedWeb(case.web_materials)
        if isinstance(config.memory, LocalMemoryConfig) and case.start_time is not None:
            raise ValueError(f"Case {case.id}: local memory mtime and processing timers require real host time; omit start_time")
        if case.initial_database is not None:
            check_initial_database(case.initial_database, config)
        if case.initial_memory is not None:
            check_memory(case.initial_memory, config, case.initial_database)
    embedding = None if config.learning is None else config.learning.embedding
    plan = {
        "set": set_name, "profile": profile,
        "voice_mode": config.evaluation.profiles[profile].voice_mode,
        "scene": config.scene, "persona": persona.id,
        "images": config.images.model_dump(mode='json'),
        "jargon_references_enabled": config.learning is not None,
        "models": {role: config.model_settings(role).model_dump(exclude={"api_key"})
                   for role in ("mind", "voice", *(("vision",) if config.models.roles.vision is not None else ()))},
        "expression_embedding": None if embedding is None else {
            "provider": embedding.provider,
            "base_url": config.models.providers[embedding.provider].base_url,
            "model": embedding.model,
            "dimensions": embedding.dimensions,
            "voice_selection_only": True,
        },
        "case_ids": [case.id for case in cases.cases],
        "case_initial_databases": {
            case.id: None if case.initial_database is None else str(case.initial_database)
            for case in cases.cases
        },
        "case_initial_memory": {case.id: None if case.initial_memory is None else
                                case.initial_memory.model_dump(mode="json") for case in cases.cases},
        "case_web_materials": {case.id: None if case.web_materials is None else str(case.web_materials)
                               for case in cases.cases},
        "case_image_materials": {case.id: None if case.image_materials is None else str(case.image_materials)
                                 for case in cases.cases},
        "case_memory_materials": {case.id: None if case.memory_materials is None else str(case.memory_materials)
                                  for case in cases.cases},
        "memory": None if config.memory is None else {
            **config.memory.model_dump(mode="json", exclude={"local": {"directory"},
                "openviking": {"scenes": {"__all__": {"api_key"}}}}),
            "initial_state": ("per-case explicit seed or empty; template directory is never used"
                              if isinstance(config.memory, LocalMemoryConfig)
                              else "per-case recorded native HTTP exchanges; no live service or async ingest")},
        "case_clocks": [
            {"case_id": case.id,
             "mode": "fixed-start-real-speed" if case.start_time is not None else "real-host-clock",
             "start_time": case.start_time}
            for case in cases.cases
        ],
        "repetitions": config.evaluation.repetitions,
        "case_timeout_seconds": config.evaluation.case_timeout_seconds,
        "max_steps_per_turn": config.max_steps,
        "turn_timeout_seconds": config.turn_timeout_seconds,
        "delivery": "simulated",
        "estimated_model_calls": None, "estimated_cost": None,
        "notice": "调用数取决于实际工具、压缩与表达向量查询，费用未核定。"
                  "配置embedding仅表示本轮可按已采用表达检索，不代表种子里已有可用向量。"
                  "固定起点按真实秒数推进；此开发回放尚非历史时机对照，完成执行不等于质量通过。",
    }
    return config, persona, cases, plan


def snapshot_persona(persona: Persona, destination: Path) -> None:
    destination.mkdir()
    write_json(destination / "persona.yaml", persona.model_dump(exclude={"voice", "boundaries", "examples"}))
    write_json(destination / "examples.yaml", [example.model_dump() for example in persona.examples])
    (destination / "voice.md").write_text(persona.voice, encoding="utf-8", newline="")
    (destination / "boundaries.md").write_text(persona.boundaries, encoding="utf-8", newline="")
    if persona.avatar is not None:
        (destination / 'avatar.png').write_bytes(persona.avatar.data)
    if persona.stickers:
        directory = destination / "stickers"
        directory.mkdir()
        write_json(directory / "index.yaml", [
            {"file": item.file, "description": item.description, "emotions": list(item.emotions),
             "tags": list(item.tags)} for item in persona.stickers.values()])
        for item in persona.stickers.values():
            path = directory / item.file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item.data)
    for filename, document in persona.knowledge.items():
        path = destination / "knowledge" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document.content, encoding="utf-8", newline="")


def snapshot_code(destination: Path) -> dict:
    package = Path(__file__).resolve().parent.parent
    # This development entry point runs from the source checkout, not a wheel.
    revision = subprocess.run(["git", "-C", str(package), "rev-parse", "HEAD"],
                              check=True, text=True, capture_output=True).stdout.strip()
    checkout = Path(subprocess.run(["git", "-C", str(package), "rev-parse", "--show-toplevel"],
                                   check=True, text=True, capture_output=True).stdout.strip())
    destination.mkdir()
    shutil.copyfile(package / '__init__.py', destination / '__init__.py')
    for module in ("next", "eval"):
        (destination / module).mkdir()
        for path in sorted((package / module).rglob("*.py")):
            target = destination / module / path.relative_to(package / module)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    common = ('media/__init__.py', 'media/images.py', 'web/auth.py', 'web/shell.py')
    for name in common:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package / name, target)
    (destination / "prompts").mkdir()
    for path in sorted(PROMPTS.glob("next_*.md")):
        shutil.copyfile(path, destination / "prompts" / path.name)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(checkout / name, destination / name)
    paths = ["src/len_bot/next", "src/len_bot/eval", "src/len_bot/prompts", "pyproject.toml", "uv.lock",
             'src/len_bot/__init__.py', *(f'src/len_bot/{name}' for name in common)]
    status = subprocess.run(["git", "-C", str(checkout), "status", "--short", "--", *paths],
                            check=True, text=True, capture_output=True).stdout
    (destination / "working-tree.txt").write_text(status, encoding="utf-8")
    return {"revision": revision, "working_tree": status,
            "note": "本次实际源码、提示词与依赖锁随附；运行期间不要修改源码。"}


def observed_database(path: Path, *, scene: str, after_turn: int = 0, after_call: int = 0,
                      after_embedding_call: int = 0, memory_baseline: MemoryBaseline | None = None) -> dict:
    if not path.exists():
        return {"database": None, "turns": None, "model_calls": None,
                "chat_model_calls": None, "embedding_model_calls": None,
                "memory_model_calls": None, "memory_errors": None, "usage": None, "cost": None}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        turns = [turn_record(row) for row in db.execute(
            "SELECT * FROM turns WHERE rowid>? ORDER BY started,id", (after_turn,),
        )]
        chat_calls = [dict(row) for row in db.execute(
            "SELECT id,turn_id,role,started,ended,usage,cost,error FROM model_calls WHERE id>? ORDER BY id", (after_call,),
        )]
        embedding_calls = [dict(row) for row in db.execute(
            "SELECT id,scene,turn_id,purpose,started,ended,usage,cost,error "
            "FROM expression_embedding_calls WHERE scene=? AND id>? ORDER BY id",
            (scene, after_embedding_call),
        )]
        for call in chat_calls:
            call["source"] = "chat"
        for call in embedding_calls:
            call["source"] = "expression_embedding"
        for call in [*chat_calls, *embedding_calls]:
            call["usage"] = None if call["usage"] is None else json.loads(call["usage"])
            call["cost"] = None if call["cost"] is None else json.loads(call["cost"])
        memory_calls, memory_errors = [], []
        memory_count = 0
        if memory_baseline is not None:
            memory_path = path.with_name(path.name + ".memory.sqlite3")
            if memory_path.exists():
                memory_calls, memory_errors = observed_memory(memory_path, memory_baseline)
                memory_count = len(memory_calls)
            else:
                memory_count, memory_errors = None, None
        calls = sorted([*chat_calls, *embedding_calls, *memory_calls],
                       key=lambda call: (call["started"], call["source"]))
        return {"database": path.name, "turns": turns, "model_calls": len(calls),
                "chat_model_calls": len(chat_calls), "embedding_model_calls": len(embedding_calls),
                "memory_model_calls": memory_count, "memory_errors": memory_errors,
                "usage": calls, "cost": cost_summary([call["cost"] for call in calls]),
                "note": "仅统计本次新增聊天、表达embedding和本地记忆调用；用量按source保留各提供方原对象，"
                        "缺失不计为零。初始历史及完整原文在数据库中。"}


async def run_case(directory: Path, config: LabConfig, persona: Persona,
                   case: ReplayCase, voice_mode: str, timeout: float,
                   initial_database: Path | None = None, initial_memory: Path | None = None,
                   web_materials: Path | None = None, image_materials: Path | None = None,
                   memory_materials: Path | None = None) -> dict:
    directory.mkdir(parents=True, mode=0o700)
    effective = config.model_dump(mode="json", exclude={"evaluation", "panel", "history_import", "reminder_import", "media_import", "media_archive", "task_archive", "memory_transfer", "persona_memory_export", "replay_clock"})
    effective.update(database="chat.sqlite3", persona="persona", voice_mode=voice_mode)
    if isinstance(config.memory, LocalMemoryConfig):
        effective["memory"]["local"]["directory"] = "memory"
    elif isinstance(config.memory, OpenVikingMemoryConfig):
        for identity in effective['memory']['openviking']['scenes'].values():
            identity['api_key'] = 'replay:' + identity['user_id']
    config_path = directory / "lenbot.config.json"
    started = time.time()
    error = None
    interruption = None
    process = None
    reader = None
    completed_turns = 0
    failed_tools = 0
    notice_errors = []
    output_closed = False
    changed = asyncio.Condition()
    processes = []
    inputs = []
    after_turn = after_call = after_embedding_call = 0
    memory_baseline = None if config.memory is None else MemoryBaseline()

    async def consume(child, stream) -> None:
        nonlocal completed_turns, failed_tools, output_closed
        try:
            pending = b""
            while chunk := await child.stdout.read(65536):
                stream.write(chunk)
                stream.flush()
                lines = (pending + chunk).split(b"\n")
                pending = lines.pop()
                for raw in lines:
                    try:
                        record = json.loads(raw)
                    except (ValueError, UnicodeDecodeError) as problem:
                        raise ValueError(f"Invalid lab stdout JSON: {problem}; raw={raw[:500]!r}") from problem
                    if record["type"] == "receipt" and record["status"] == "error":
                        raise ValueError(f"Lab rejected replay input: {record['error']}; {record['input_fragment']}")
                    if record["type"] == "notice" and record["error"] is not None:
                        notice_errors.append(record["error"])
                    if record["type"] == "turn":
                        async with changed:
                            completed_turns += 1
                            failed_tools += record["failed_tools"]
                            changed.notify_all()
            if pending:
                raise ValueError(f"Lab stdout ended before LF: raw={pending[:500]!r}")
            await child.wait()
            if child.returncode != 0:
                raise RuntimeError(f"Lab process exited with {child.returncode}; see stderr.log")
        finally:
            async with changed:
                output_closed = True
                changed.notify_all()

    async def start(tasks, stdout, stderr) -> None:
        nonlocal process, reader, output_closed
        output_closed = False
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "len_bot.next.trials.lab", cwd=directory,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=stderr,
        )
        processes.append({"pid": process.pid, "started": time.time(), "returncode": None})
        reader = tasks.create_task(consume(process, stdout))

    async def finish() -> None:
        process.stdin.close()
        await process.wait()
        await reader
        processes[-1].update(ended=time.time(), returncode=process.returncode)

    try:
        snapshot_persona(persona, directory / "persona")
        write_json(directory / "case.json", case.model_dump(mode="json"))
        if initial_database is not None:
            with closing(sqlite3.connect(initial_database.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
                after_turn = db.execute("SELECT COALESCE(MAX(rowid),0) FROM turns").fetchone()[0]
                after_call = db.execute("SELECT COALESCE(MAX(id),0) FROM model_calls").fetchone()[0]
                after_embedding_call = db.execute(
                    "SELECT COALESCE(MAX(id),0) FROM expression_embedding_calls"
                ).fetchone()[0]
            shutil.copyfile(initial_database, directory / "chat.sqlite3")
        if initial_memory is not None:
            memory_baseline = install_memory(initial_memory, directory)
        if web_materials is not None:
            shutil.copytree(web_materials.parent, directory / "web-materials")
            effective['replay_web'] = 'web-materials/manifest.json'
        if image_materials is not None:
            shutil.copytree(image_materials.parent, directory / 'image-materials')
            effective['replay_images'] = 'image-materials/manifest.json'
        if memory_materials is not None:
            shutil.copytree(memory_materials.parent, directory / 'memory-materials')
            effective['replay_memory'] = 'memory-materials/manifest.json'
        # The same per-repeat anchor stays in this child config across normal restarts.
        effective["replay_clock"] = (None if case.start_time is None else {
            "epoch": case.start_time, "monotonic_origin": time.monotonic(),
        })
        write_json(config_path, effective)
        config_path.chmod(0o600)
        with (directory / "stdout.jsonl").open("wb") as stdout, \
             (directory / "stderr.log").open("wb") as stderr:
            async with asyncio.timeout(timeout):
                async with asyncio.TaskGroup() as tasks:
                    await start(tasks, stdout, stderr)
                    for index, step in enumerate(case.steps):
                        inputs.append({"step": index, "type": step.type, "at": time.time()})
                        if step.type == "message":
                            process.stdin.write((encode(step.event) + "\n").encode())
                            await process.stdin.drain()
                        elif step.type == "observe":
                            await asyncio.sleep(step.seconds)
                        elif step.type == "await_turn":
                            async with changed:
                                await changed.wait_for(lambda: completed_turns >= step.count or output_closed)
                            if completed_turns < step.count:
                                raise EOFError(f"Lab output ended after {completed_turns} turns, expected at least {step.count}; see stderr.log")
                        else:
                            await finish()
                            await start(tasks, stdout, stderr)
                    await finish()
    except asyncio.CancelledError as problem:
        interruption = problem
        error = traceback.format_exc()
    except Exception:
        error = traceback.format_exc()
    finally:
        if process is not None:
            if process.returncode is None:
                process.kill()
            # The reader may have been cancelled with the case. Drain the finite
            # remaining pipe before wait(), retaining even an incomplete JSON line.
            with (directory / "stdout.jsonl").open("ab") as remaining:
                while chunk := await process.stdout.read(65536):
                    remaining.write(chunk)
            await process.wait()
            processes[-1].update(ended=time.time(), returncode=process.returncode)
        # Only this generated child's configuration is removed; the source root is unchanged.
        for provider in effective["models"]["providers"].values():
            del provider["api_key"]
        if isinstance(config.memory, OpenVikingMemoryConfig):
            for identity in effective['memory']['openviking']['scenes'].values():
                del identity['api_key']
        try:
            write_json(directory / "config.snapshot.json", effective)
        finally:
            config_path.unlink(missing_ok=True)
    consumption_path = directory / 'memory-materials/consumption.json'
    consumption = (MemoryConsumption.model_validate_json(consumption_path.read_bytes()).model_dump()
                   if consumption_path.is_file() else None)
    result = {"case_id": case.id, "script_completed": error is None, "error": error,
              "initial_database": None if initial_database is None else "../initial.sqlite3",
              "initial_memory": None if initial_memory is None else "../initial-memory",
              "web_materials": None if web_materials is None else "../web-materials/manifest.json",
              "image_materials": None if image_materials is None else "../image-materials/manifest.json",
              "memory_materials": None if memory_materials is None else "../memory-materials/manifest.json",
              "native_memory_consumption": consumption,
              "clock": {"mode": "fixed-start-real-speed" if case.start_time is not None else "real-host-clock",
                        "start_time": case.start_time},
              "time_axes": {
                  "native_memory_responses": None if memory_materials is None else "frozen-original-received_at",
                  "started_ended_inputs_processes": "host-physical-unix-seconds",
                  "database_turns_and_model_calls": (
                      "fixed-start-replay-unix-seconds" if case.start_time is not None
                      else "host-physical-unix-seconds"),
                  "database_embedding_calls": (
                      "fixed-start-replay-unix-seconds" if case.start_time is not None
                      else "host-physical-unix-seconds"),
              },
              "started": started, "ended": time.time(), "inputs": inputs, "processes": processes,
              "failed_tools": failed_tools,
              "notice_errors": notice_errors,
              **observed_database(directory / "chat.sqlite3", scene=config.scene,
                                  after_turn=after_turn, after_call=after_call,
                                  after_embedding_call=after_embedding_call, memory_baseline=memory_baseline)}
    write_json(directory / "result.json", result)
    if interruption is not None:
        raise interruption
    return result


async def run(config: LabConfig, persona: Persona, cases: CaseFile, plan: dict) -> tuple[Path, bool]:
    settings = config.evaluation
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = settings.runs_directory / run_id
    destination.mkdir(parents=True, mode=0o700)
    code = snapshot_code(destination / "source")
    write_json(destination / "cases.json", cases.model_dump(mode="json"))
    metadata = {"id": run_id, "plan": plan, "code": code, "started": time.time(), "ended": None,
                "completed": False}
    write_json(destination / "run.json", metadata)
    annotations = [{"case_id": case.id, "repeat": repeat, "verdict": "unreviewed", "reason": ""}
                   for case in cases.cases for repeat in range(1, settings.repetitions + 1)]
    write_json(destination / "annotations.json", annotations)
    failed = False
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, asyncio.current_task().cancel)
    try:
        for case in cases.cases:
            (destination / case.id).mkdir()
            initial_database = None
            initial_memory = None
            web_materials = None
            image_materials = None
            memory_materials = None
            if case.initial_database is not None:
                initial_database = destination / case.id / "initial.sqlite3"
                check_initial_database(case.initial_database, config)
                shutil.copyfile(case.initial_database, initial_database)
            if case.initial_memory is not None:
                check_memory(case.initial_memory, config, case.initial_database)
                initial_memory = destination / case.id / "initial-memory"
                freeze_memory(case.initial_memory, initial_memory)
                check_memory(InitialMemory(directory=str(initial_memory / "memory"),
                                           jobs=str(initial_memory / "jobs.sqlite3")), config, initial_database)
            if case.web_materials is not None:
                web_materials = RecordedWeb(case.web_materials).freeze(destination / case.id / 'web-materials')
            if case.image_materials is not None:
                image_materials = RecordedImages(case.image_materials, max_bytes=config.images.max_bytes).freeze(
                    destination / case.id / 'image-materials')
            if case.memory_materials is not None:
                recordings = RecordedMemory(case.memory_materials)
                recordings.check_settings(config.memory.openviking)
                memory_materials = recordings.freeze(destination / case.id / 'memory-materials')
            for repeat in range(1, settings.repetitions + 1):
                result = await run_case(destination / case.id / str(repeat), config, persona, case,
                                        plan["voice_mode"], settings.case_timeout_seconds,
                                        initial_database, initial_memory, web_materials, image_materials, memory_materials)
                failed |= (not result["script_completed"] or result["failed_tools"] > 0 or bool(result["notice_errors"])
                           or (result["turns"] is not None and any(turn["error"] is not None for turn in result["turns"]))
                           or (result["usage"] is not None and any(call["error"] is not None for call in result["usage"])))
                failed |= bool(result["memory_errors"])
                print(encode({"run": run_id, "case": case.id, "repeat": repeat,
                              "script_completed": result["script_completed"], "error": result["error"]}), flush=True)
        metadata["completed"] = True
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        metadata["ended"] = time.time()
        write_json(destination / "run.json", metadata)
    return destination, failed


def run_directory(config: LabConfig, run_id: str) -> Path:
    if re.fullmatch(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z", run_id) is None:
        raise ValueError("run must be an exact run directory name")
    if config.evaluation is None:
        raise ValueError("Root configuration has no evaluation settings")
    return config.evaluation.runs_directory / run_id


def read_report(directory: Path) -> dict:
    metadata = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    annotation_path = directory / "annotations.json"
    raw = annotation_path.read_text(encoding="utf-8")
    try:
        annotations = TypeAdapter(list[Annotation]).validate_json(raw)
    except ValidationError as error:
        raise ValueError(f"{annotation_path}: {error}; raw={raw[:500]!r}") from error
    expected = {(case, repeat) for case in metadata["plan"]["case_ids"]
                for repeat in range(1, metadata["plan"]["repetitions"] + 1)}
    reviewed = {}
    for annotation in annotations:
        key = (annotation.case_id, annotation.repeat)
        if key not in expected or key in reviewed:
            raise ValueError(f"Unknown or duplicate annotation: {annotation!r}")
        reviewed[key] = annotation
    if set(reviewed) != expected:
        raise ValueError(f"Missing annotations: {sorted(expected - set(reviewed))!r}")
    results = []
    counts = dict.fromkeys(("unreviewed", "pass", "fail", "uncertain"), 0)
    for case, repeat in sorted(expected):
        path = directory / case / str(repeat) / "result.json"
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        annotation = reviewed[case, repeat]
        if result is None and annotation.verdict != "unreviewed":
            raise ValueError(f"Cannot score an unfinished case: {case}/{repeat}")
        if result is not None and not result["script_completed"] and annotation.verdict == "pass":
            raise ValueError(f"Cannot pass an incomplete script: {case}/{repeat}")
        counts[annotation.verdict] += 1
        results.append({"case_id": case, "repeat": repeat, "result": result,
                        "annotation": annotation.model_dump()})
    scored = counts["pass"] + counts["fail"]
    costs = []
    unobserved_cases = 0
    for item in results:
        result = item["result"]
        if result is None or result["usage"] is None:
            unobserved_cases += 1
        elif result["cost"] is None:
            costs.extend([None] * len(result["usage"]))
        else:
            costs.extend(call["cost"] for call in result["usage"])
    summary = {"run": metadata, "quality": counts, "scored": scored,
               "pass_rate": None if scored == 0 else counts["pass"] / scored,
               "cost": {**cost_summary(costs), "unobserved_cases": unobserved_cases}, "results": results,
               "notice": "1 倍速开发回放；每例时间轴见结果，未标注不计通过，费用未知不计零，完整输出见各实例数据库。"}
    return summary


def report(root: Path, run_id: str) -> dict:
    directory = run_directory(load_config(root), run_id)
    summary = read_report(directory)
    write_json(directory / "report.json", summary)
    return summary


def changed_files(left: Path, right: Path) -> list[str]:
    names = {str(path.relative_to(root)) for root in (left, right)
             for path in root.rglob("*") if path.is_file()}
    return [name for name in sorted(names)
            if not (left / name).is_file() or not (right / name).is_file()
            or not filecmp.cmp(left / name, right / name, shallow=False)]


def comparison_side(item: dict) -> dict:
    result = item["result"]
    return {
        "annotation": item["annotation"],
        "execution": None if result is None else {
            "script_completed": result["script_completed"], "script_error": result["error"],
            "failed_tools": result["failed_tools"], "notice_errors": result["notice_errors"],
            "turn_errors": None if result["turns"] is None else [
                turn["error"] for turn in result["turns"] if turn["error"] is not None],
            "model_errors": None if result["usage"] is None else [
                call["error"] for call in result["usage"] if call["error"] is not None],
            "memory_errors": result.get("memory_errors"),
        },
        "model_calls": None if result is None else result["model_calls"],
        "memory_model_calls": None if result is None else result.get("memory_model_calls"),
        "turns": None if result is None else result["turns"],
        "script_seconds": None if result is None else result["ended"] - result["started"],
        "cost": None if result is None else result["cost"],
    }


def compare(root: Path, baseline_id: str, candidate_id: str) -> dict:
    config = load_config(root)
    left, right = (run_directory(config, run_id) for run_id in (baseline_id, candidate_id))
    baseline, candidate = read_report(left), read_report(right)
    cases = [CaseFile.model_validate(json.loads((directory / "cases.json").read_text(encoding="utf-8")))
             for directory in (left, right)]
    definitions = [{case.id: case for case in file.cases} for file in cases]
    if (baseline["run"]["plan"]["set"] != candidate["run"]["plan"]["set"]
            or definitions[0].keys() != definitions[1].keys()):
        raise ValueError("Comparison requires the same evaluation set and case ids")
    initial_histories = {}
    initial_memories = {}
    web_materials = {}
    image_materials = {}
    memory_materials = {}
    for name in definitions[0]:
        a, b = definitions[0][name], definitions[1][name]
        if a.model_dump(exclude={"initial_database", "initial_memory", "web_materials", "image_materials", "memory_materials"}) != b.model_dump(exclude={"initial_database", "initial_memory", "web_materials", "image_materials", "memory_materials"}):
            raise ValueError(f"Replay case definition differs: {name}; not pairing different inputs or expectations")
        if (a.initial_database is None) != (b.initial_database is None):
            raise ValueError(f"Archived initial database differs: {name}; not pairing different starting histories")
        if (a.initial_memory is None) != (b.initial_memory is None):
            raise ValueError(f"Initial memory presence differs: {name}; not pairing different starting memories")
        if (a.memory_materials is None) != (b.memory_materials is None):
            raise ValueError(f'Native memory material presence differs: {name}; not pairing different service inputs')
        if a.memory_materials is None:
            memory_materials[name] = 'both-absent'
        else:
            available = []
            for directory, summary in ((left, baseline), (right, candidate)):
                manifest = directory / name / 'memory-materials/manifest.json'
                if not manifest.is_file() and any(item['case_id'] == name and item['result'] is not None
                                                 for item in summary['results']):
                    raise ValueError(f'Executed case is missing its native memory archive: {manifest}')
                available.append(manifest.is_file())
                if manifest.is_file():
                    RecordedMemory(manifest)
            if all(available):
                if changed_files(left / name / 'memory-materials', right / name / 'memory-materials'):
                    raise ValueError(f'Archived native memory responses differ: {name}; not pairing different material')
                memory_materials[name] = 'same-archived-bytes'
            else:
                memory_materials[name] = None
        if (a.image_materials is None) != (b.image_materials is None):
            raise ValueError(f'Image material presence differs: {name}; not pairing different originals')
        if a.image_materials is None:
            image_materials[name] = 'both-absent'
        else:
            available = []
            for directory, summary in ((left, baseline), (right, candidate)):
                manifest = directory / name / 'image-materials/manifest.json'
                if not manifest.is_file() and any(item['case_id'] == name and item['result'] is not None
                                                 for item in summary['results']):
                    raise ValueError(f'Executed case is missing its image archive: {manifest}')
                available.append(manifest.is_file())
                if manifest.is_file():
                    RecordedImages(manifest, max_bytes=summary['run']['plan']['images']['max_bytes'])
            if all(available):
                if changed_files(left / name / 'image-materials', right / name / 'image-materials'):
                    raise ValueError(f'Archived image originals differ: {name}; not pairing different pixels')
                image_materials[name] = 'same-archived-bytes'
            else:
                image_materials[name] = None
        if (a.web_materials is None) != (b.web_materials is None):
            raise ValueError(f"Web material presence differs: {name}; not pairing different fixed responses")
        if a.web_materials is None:
            web_materials[name] = 'both-absent'
        else:
            available = []
            for directory, summary in ((left, baseline), (right, candidate)):
                manifest = directory / name / 'web-materials/manifest.json'
                if not manifest.is_file() and any(item['case_id'] == name and item['result'] is not None
                                                 for item in summary['results']):
                    raise ValueError(f'Executed case is missing its web archive: {manifest}')
                available.append(manifest.is_file())
                if manifest.is_file():
                    RecordedWeb(manifest)
            if all(available):
                if changed_files(left / name / 'web-materials', right / name / 'web-materials'):
                    raise ValueError(f'Archived web responses differ: {name}; not pairing different fixed material')
                web_materials[name] = 'same-archived-bytes'
            else:
                web_materials[name] = None
        if a.initial_memory is None:
            initial_memories[name] = "both-empty"
        else:
            available = []
            for directory, summary in ((left, baseline), (right, candidate)):
                path = directory / name / "initial-memory"
                if not path.is_dir() and any(item["case_id"] == name and item["result"] is not None
                                             for item in summary["results"]):
                    raise ValueError(f"Executed case is missing its memory archive: {path}")
                available.append(path.is_dir())
            if all(available):
                if not same_memory(left / name / "initial-memory", right / name / "initial-memory"):
                    raise ValueError(f"Archived initial memory differs: {name}; not pairing different starting memories")
                initial_memories[name] = "same-archived-bytes-and-markdown-times"
            else:
                initial_memories[name] = None
        if a.initial_database is None:
            initial_histories[name] = "both-empty"
        else:
            available = []
            for directory, summary in ((left, baseline), (right, candidate)):
                path = directory / name / "initial.sqlite3"
                if not path.is_file() and any(item["case_id"] == name and item["result"] is not None
                                             for item in summary["results"]):
                    raise ValueError(f"Executed case is missing its initial archive: {path}")
                available.append(path.is_file())
            if all(available):
                if not filecmp.cmp(left / name / "initial.sqlite3", right / name / "initial.sqlite3", shallow=False):
                    raise ValueError(f"Archived initial database differs: {name}; not pairing different starting histories")
                initial_histories[name] = "same-archived-bytes"
            else:
                initial_histories[name] = None
    sides = [{(item["case_id"], item["repeat"]): item for item in summary["results"]}
             for summary in (baseline, candidate)]
    transitions = dict.fromkeys(("pass_to_pass", "pass_to_fail", "fail_to_pass", "fail_to_fail"), 0)
    paired_scored = 0
    unpaired = {"baseline": 0, "candidate": 0}
    pairs = []
    for name, repeat in sorted(sides[0].keys() | sides[1].keys()):
        key = (name, repeat)
        a = comparison_side(sides[0][key]) if key in sides[0] else None
        b = comparison_side(sides[1][key]) if key in sides[1] else None
        transition = None
        if a is None:
            unpaired["candidate"] += 1
        elif b is None:
            unpaired["baseline"] += 1
        elif a["annotation"]["verdict"] in {"pass", "fail"} and b["annotation"]["verdict"] in {"pass", "fail"}:
            transition = a["annotation"]["verdict"] + "_to_" + b["annotation"]["verdict"]
            transitions[transition] += 1
            paired_scored += 1
        instances = [directory / name / str(repeat) for directory in (left, right)]
        config_changes = persona_changes = None
        if all((instance / "config.snapshot.json").is_file() for instance in instances):
            configs = [json.loads((instance / "config.snapshot.json").read_text(encoding="utf-8"))
                       for instance in instances]
            for settings in configs:
                if settings["replay_clock"] is not None:
                    del settings["replay_clock"]["monotonic_origin"]
            config_changes = []
            for field in sorted(configs[0].keys() | configs[1].keys()):
                if field not in configs[0]:
                    config_changes.append({"field": field, "change": "added", "candidate": configs[1][field]})
                elif field not in configs[1]:
                    config_changes.append({"field": field, "change": "removed", "baseline": configs[0][field]})
                elif configs[0][field] != configs[1][field]:
                    config_changes.append({"field": field, "change": "changed",
                                           "baseline": configs[0][field], "candidate": configs[1][field]})
            if all((instance / "persona").is_dir() for instance in instances):
                persona_changes = changed_files(instances[0] / "persona", instances[1] / "persona")
        deltas = {field: None if a is None or b is None or a[field] is None or b[field] is None
                  else b[field] - a[field] for field in ("model_calls", "script_seconds")}
        pairs.append({"case_id": name, "repeat": repeat, "baseline": a, "candidate": b,
                      "annotation_transition": transition, "deltas": deltas,
                      "configuration_changes": config_changes, "persona_changed_files": persona_changes,
                      "archives": {"baseline": str(instances[0]), "candidate": str(instances[1])}})
    result = {
        "baseline": {key: baseline[key] for key in ("run", "quality", "scored", "pass_rate", "cost")},
        "candidate": {key: candidate[key] for key in ("run", "quality", "scored", "pass_rate", "cost")},
        "paired_scored": paired_scored, "annotation_transitions": transitions, "unpaired": unpaired,
        "paired_pass_rate_delta": (None if paired_scored == 0 else
                                   (transitions["fail_to_pass"] - transitions["pass_to_fail"]) / paired_scored),
        "pairs": pairs, "initial_histories": initial_histories, "initial_memories": initial_memories,
        "web_materials": web_materials,
        "image_materials": image_materials,
        "memory_materials": memory_materials,
        "source_changed_files": changed_files(left / "source", right / "source"),
        "notice": "同输入的配对人工判定观察，不是架构或模型优势结论。未完成脚本的人工fail仍保留；"
                  "未评、uncertain和未配对不混入分母。配置和源码差异须结合原档案解释；"
                  "脚本总耗时不是首答延迟，缺省宿主日期不是固定历史时机，未知费用不计零。",
    }
    write_json(right / f"comparison-{baseline_id}.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("set")
        command.add_argument("--profile", required=True)
    commands.add_parser("report").add_argument("run")
    comparison = commands.add_parser("compare")
    comparison.add_argument("baseline")
    comparison.add_argument("candidate")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if args.command == "report":
        print(encode(report(root, args.run)))
        return
    if args.command == "compare":
        print(encode(compare(root, args.baseline, args.candidate)))
        return
    config, persona, cases, plan = prepare(root, args.set, args.profile)
    print(encode(plan), flush=True)
    if args.command == "plan":
        return
    if input("确认已有本次模型调用授权后，输入 run 开始；其他输入取消：") != "run":
        print("Cancelled; no model requests started.")
        return
    destination, failed = asyncio.run(run(config, persona, cases, plan))
    print(encode({"run_directory": str(destination), "execution_errors": failed}))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
