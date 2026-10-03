"""Explicit stopped-host conversion of native mind history to portable text."""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo

from .chat_context import PROMPTS, build_system
from .chat_tools import build_tools
from .config import HostConfig, load_host_config
from .instance_lock import instance_lock
from .context import complete_boundaries, estimate_request, project_history, recap_source
from .discovery import DEFERRED_NAMES
from .memory import LocalMemoryConfig
from .memory_local import scene_overview
from .persona import Persona, load_persona
from .schedule import describe
from .store import FORMAT_VERSION, Store, encode
from .schedule_store import ScheduleStore


def _binding(config: HostConfig) -> tuple[str, str, str]:
    model = config.model_settings("mind")
    return model.api, model.base_url, model.model


def _status(store: Store, scene: str) -> None:
    call = store.db.execute(
        "SELECT model_calls.id,model_calls.role FROM model_calls "
        "JOIN turns ON turns.id=model_calls.turn_id "
        "WHERE turns.scene=? AND model_calls.ended IS NULL LIMIT 1", (scene,),
    ).fetchone()
    if call is not None:
        raise ValueError(f"Scene {scene} has unfinished {call['role']} model call {call['id']}")
    turn = store.db.execute(
        "SELECT id,status FROM turns WHERE scene=? AND ended IS NULL "
        "AND status IN ('running','settling') LIMIT 1", (scene,),
    ).fetchone()
    if turn is not None:
        raise ValueError(f"Scene {scene} has unfinished turn {turn['id']} in {turn['status']}")


def _state(store: Store, config: HostConfig, scene: str, persona: Persona) -> dict:
    local = config.scene_config(scene)
    now = datetime.now(ZoneInfo(config.timezone)).isoformat(timespec="seconds")
    content = f"当前时间：{now}"
    schedules = ScheduleStore(store).list_schedules(scene, limit=21)
    if schedules:
        content += "\n<未完成安排>\n" + "\n\n".join(
            describe(item, preview=True) for item in schedules[:20]) + "\n</未完成安排>"
        if len(schedules) > 20:
            content += "\n这里只列前 20 条；schedule_list 可继续查看。"
    if local.voice_mode == "direct":
        variants = [style for style in persona.styles if style.weight > 0]
        if variants:
            template = Template((PROMPTS / "next_style.md").read_text())
            expression = max(
                (template.substitute(name=style.name, note="" if style.note is None else style.note)
                 for style in variants), key=lambda text: len(text.encode("utf-8")),
            )
            content += "\n" + expression
    return {"role": "user", "content": content}


def _check_budget(store: Store, config: HostConfig, scene: str, persona: Persona,
                  recap: str | None, entries: list[tuple[int, dict]]) -> int:
    local = config.scene_config(scene)
    allowed = build_tools(local, persona, platform=config.delivery == "onebot")
    profile = (scene_overview(config.memory.local.directory, scene)
               if isinstance(config.memory, LocalMemoryConfig) and config.memory.summaries else None)
    system = build_system(local, persona, allowed, platform=config.delivery == "onebot", group_profile=profile)
    tools = [tool for tool in allowed if tool["function"]["name"] not in DEFERRED_NAMES]
    binding = config.models.roles.mind
    trigger = int(binding.context_window_tokens * config.compaction.trigger_ratio)
    messages = ([{"role": "system", "content": system}]
                + project_history(recap, entries) + [_state(store, config, scene, persona)])
    estimated = estimate_request(messages, tools, binding.max_output_tokens)
    if estimated > trigger:
        raise ValueError(
            f"Scene {scene} portable first mind request estimates {estimated} tokens including output "
            f"reserve, above configured trigger {trigger}; source history unchanged"
        )
    return estimated


def _backup(store: Store, path: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = path.with_name(path.name + f".portable-{stamp}.bak")
    with backup_path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(backup_path)) as backup:
            store.db.backup(backup)
    except BaseException:
        backup_path.unlink()
        raise
    return backup_path


def convert(config: HostConfig) -> dict:
    """Prepare every changed scene, then commit all portable cutpoints together."""
    path = config.database
    if not path.is_file():
        raise ValueError(f"Next-core database does not exist: {path}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists() and sidecar.stat().st_size:
            raise ValueError(f"Database has a nonempty {suffix} file; stop the host and use a complete snapshot: {path}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as check:
        if (check.execute("PRAGMA application_id").fetchone()[0] != 0x4C424E31
                or check.execute("PRAGMA user_version").fetchone()[0] != FORMAT_VERSION):
            raise ValueError(f"Not a current next-core database: {path}")

    personas = {settings.persona: load_persona(settings.persona)
                for settings in config.scenes.values()}
    for scene, settings in config.scenes.items():
        build_tools(config.scene_config(scene), personas[settings.persona],
                    platform=config.delivery == "onebot")
    updates: dict[str, tuple[int, int, str]] = {}
    reports: dict[str, dict] = {}
    target_binding = _binding(config)
    with Store(path) as store:
        store.db.execute("PRAGMA busy_timeout=0")
        for scene, settings in config.scenes.items():
            previous = store.last_mind_request(scene)
            if previous is None:
                reports[scene] = {"status": "no_previous_mind_request"}
                continue
            old = tuple(previous["settings"][key] for key in ("api", "base_url", "model"))
            if old == target_binding:
                reports[scene] = {"status": "binding_unchanged"}
                continue
            _status(store, scene)
            recap, entries = store.active_history(scene)
            persona = personas[settings.persona]
            if any(message["role"] in {"assistant", "tool"} for _, message in entries):
                try:
                    boundaries = complete_boundaries(entries)
                except (KeyError, ValueError) as error:
                    raise ValueError(f"Scene {scene} native tool group is not complete: {error}") from error
                if not boundaries or boundaries[-1] != len(entries):
                    raise ValueError(f"Scene {scene} has an incomplete native tool group at the active tail")
                portable = recap_source(recap, entries)
                estimated = _check_budget(store, config, scene, persona, portable, [])
                session = store.db.execute(
                    "SELECT compact_through FROM mind_sessions WHERE scene=?", (scene,),
                ).fetchone()
                old_through = 0 if session is None else session[0]
                updates[scene] = (old_through, entries[-1][0], portable)
                reports[scene] = {"status": "converted", "through": entries[-1][0],
                                  "estimated_first_request_tokens": estimated}
            else:
                estimated = _check_budget(store, config, scene, persona, recap, entries)
                reports[scene] = {"status": "already_portable",
                                  "estimated_first_request_tokens": estimated}
        if not updates:
            return {"database": str(path), "backup": None, "scenes": reports}
        backup = _backup(store, path)
        store.install_portable_recaps(updates)
    return {"database": str(path), "backup": str(backup), "scenes": reports}


def main() -> None:
    if len(sys.argv) != 1:
        raise SystemExit("Portable history conversion takes no arguments; run from the stopped host root")
    with instance_lock(Path.cwd()):
        config = load_host_config(Path.cwd())
        print(encode(convert(config)), flush=True)


if __name__ == "__main__":
    main()
