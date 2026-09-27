"""Run structured development cases through the unchanged isolated lab entry point."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from .cases import CaseFile, ReplayCase, load_cases
from ..next.chat import PROMPTS
from ..next.config import LabConfig, load_config
from ..next.persona import Persona, load_persona
from ..next.store import encode


LOCAL_TOOLS = {"say", "wait", "recall_chat", "schedule", "schedule_list", "schedule_cancel",
               "persona_knowledge", "tool_search"}


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


def prepare(root: Path, set_name: str, profile: str) -> tuple[LabConfig, Persona, CaseFile, dict]:
    config = load_config(root)
    if config.replay_clock is not None:
        raise ValueError("Evaluation template root must not contain replay_clock; each repeat creates its own anchor")
    if config.evaluation is None:
        raise ValueError("Root configuration has no evaluation settings")
    if set_name not in config.evaluation.sets or profile not in config.evaluation.profiles:
        raise ValueError(f"Unknown configured evaluation set/profile: {set_name!r}/{profile!r}")
    if config.onebot is not None or config.delivery != "simulated" or config.panel is not None:
        raise ValueError("Development replay requires onebot=null, delivery=simulated and panel=null")
    if config.web_read is not None or config.models.roles.vision is not None:
        raise ValueError("Development replay does not yet provide fixed web/vision tool material")
    persona = load_persona(config.persona)
    if persona.tools != "all" and (unsupported := set(persona.tools) - LOCAL_TOOLS):
        raise ValueError(f"Development replay does not implement these declared tools: {sorted(unsupported)}")
    cases = load_cases(config.evaluation.sets[set_name], set_name=set_name,
                       scene=config.scene, bot_qq=config.bot_qq)
    plan = {
        "set": set_name, "profile": profile,
        "voice_mode": config.evaluation.profiles[profile].voice_mode,
        "scene": config.scene, "persona": persona.id,
        "models": {role: config.model_settings(role).model_dump(exclude={"api_key"})
                   for role in ("mind", "voice")},
        "case_ids": [case.id for case in cases.cases],
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
        "notice": "调用数取决于实际工具与压缩，费用未核定。固定起点按真实秒数推进；此开发回放尚非历史时机对照，完成执行不等于质量通过。",
    }
    return config, persona, cases, plan


def snapshot_persona(persona: Persona, destination: Path) -> None:
    destination.mkdir()
    write_json(destination / "persona.yaml", persona.model_dump(exclude={"voice", "boundaries", "examples"}))
    write_json(destination / "examples.yaml", [example.model_dump() for example in persona.examples])
    (destination / "voice.md").write_text(persona.voice, encoding="utf-8", newline="")
    (destination / "boundaries.md").write_text(persona.boundaries, encoding="utf-8", newline="")
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
    for module in ("next", "eval"):
        (destination / module).mkdir()
        for path in sorted((package / module).glob("*.py")):
            shutil.copyfile(path, destination / module / path.name)
    (destination / "prompts").mkdir()
    for path in sorted(PROMPTS.glob("next_*.md")):
        shutil.copyfile(path, destination / "prompts" / path.name)
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(checkout / name, destination / name)
    paths = ["src/len_bot/next", "src/len_bot/eval", "src/len_bot/prompts", "pyproject.toml", "uv.lock"]
    status = subprocess.run(["git", "-C", str(checkout), "status", "--short", "--", *paths],
                            check=True, text=True, capture_output=True).stdout
    (destination / "working-tree.txt").write_text(status, encoding="utf-8")
    return {"revision": revision, "working_tree": status,
            "note": "本次实际源码、提示词与依赖锁随附；运行期间不要修改源码。"}


def observed_database(path: Path) -> dict:
    if not path.exists():
        return {"database": None, "turns": None, "model_calls": None, "usage": None}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        turns = [dict(row) for row in db.execute("SELECT * FROM turns ORDER BY started,id")]
        calls = [dict(row) for row in db.execute(
            "SELECT id,turn_id,role,started,ended,usage,error FROM model_calls ORDER BY id")]
        for call in calls:
            call["usage"] = None if call["usage"] is None else json.loads(call["usage"])
        return {"database": path.name, "turns": turns, "model_calls": len(calls),
                "usage": calls, "cost": None,
                "note": "用量保留提供方原对象，缺失不计为零；原请求、响应与工具结果在数据库中。"}


async def run_case(directory: Path, config: LabConfig, persona: Persona,
                   case: ReplayCase, voice_mode: str, timeout: float) -> dict:
    directory.mkdir(parents=True, mode=0o700)
    effective = config.model_dump(mode="json", exclude={"evaluation", "panel", "history_import", "history_export", "replay_clock"})
    effective.update(database="chat.sqlite3", persona="persona", voice_mode=voice_mode)
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
            sys.executable, "-m", "len_bot.next.lab", cwd=directory,
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
        try:
            write_json(directory / "config.snapshot.json", effective)
        finally:
            config_path.unlink(missing_ok=True)
    result = {"case_id": case.id, "script_completed": error is None, "error": error,
              "clock": {"mode": "fixed-start-real-speed" if case.start_time is not None else "real-host-clock",
                        "start_time": case.start_time},
              "time_axes": {
                  "started_ended_inputs_processes": "host-physical-unix-seconds",
                  "database_turns_and_model_calls": (
                      "fixed-start-replay-unix-seconds" if case.start_time is not None
                      else "host-physical-unix-seconds"),
              },
              "started": started, "ended": time.time(), "inputs": inputs, "processes": processes,
              "failed_tools": failed_tools,
              "notice_errors": notice_errors,
              **observed_database(directory / "chat.sqlite3")}
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
            for repeat in range(1, settings.repetitions + 1):
                result = await run_case(destination / case.id / str(repeat), config, persona, case,
                                        plan["voice_mode"], settings.case_timeout_seconds)
                failed |= (not result["script_completed"] or result["failed_tools"] > 0 or bool(result["notice_errors"])
                           or (result["turns"] is not None and any(turn["error"] is not None for turn in result["turns"]))
                           or (result["usage"] is not None and any(call["error"] is not None for call in result["usage"])))
                print(encode({"run": run_id, "case": case.id, "repeat": repeat,
                              "script_completed": result["script_completed"], "error": result["error"]}), flush=True)
        metadata["completed"] = True
    finally:
        loop.remove_signal_handler(signal.SIGTERM)
        metadata["ended"] = time.time()
        write_json(destination / "run.json", metadata)
    return destination, failed


def report(root: Path, run_id: str) -> dict:
    if re.fullmatch(r"[0-9]{8}T[0-9]{6}\.[0-9]{6}Z", run_id) is None:
        raise ValueError("run must be an exact run directory name")
    config = load_config(root)
    if config.evaluation is None:
        raise ValueError("Root configuration has no evaluation settings")
    directory = config.evaluation.runs_directory / run_id
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
    summary = {"run": metadata, "quality": counts, "scored": scored,
               "pass_rate": None if scored == 0 else counts["pass"] / scored,
               "cost": None, "results": results,
               "notice": "1 倍速开发回放；每例时间轴见结果，未标注不计通过，费用未知不计零，完整输出见各实例数据库。"}
    write_json(directory / "report.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "run"):
        command = commands.add_parser(name)
        command.add_argument("set")
        command.add_argument("--profile", required=True)
    commands.add_parser("report").add_argument("run")
    args = parser.parse_args()
    root = Path.cwd().resolve()
    if args.command == "report":
        print(encode(report(root, args.run)))
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
