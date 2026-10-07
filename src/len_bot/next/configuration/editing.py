"""Shared root-file reads, validated edits and pending restart differences."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from ..config import HostConfig, _load_host_source, _read_root
from ..persona.profile import Persona, load_persona
from ..tools.skills import select_skills
from ..plugins.manifest import scene_skill_catalog
from ..plugins.install import PluginInstaller
from .. import prompt_files
from ..storage.files import sync_directory


def restart_summary(root: Path, running: HostConfig, personas: dict[str, Persona]) -> dict:
    """Which parts of the saved root config and persona packages differ from what is running."""
    saved = _read_saved(root)
    loaded: dict[Path, Persona] = {}
    changed_personas = {}
    for scene, settings in saved.scenes.items():
        if scene not in personas:
            continue
        if settings.persona not in loaded:
            loaded[settings.persona] = load_persona(settings.persona)
        if loaded[settings.persona] != personas[scene]:
            changed_personas[str(settings.persona)] = loaded[settings.persona].name
    return {
        "plugins": [item.model_dump() for item in PluginInstaller(root).pending() if item.requested],
        "sections": [name for name in HostConfig.model_fields
                     if name != "scenes" and getattr(running, name) != getattr(saved, name)]
                    + (["prompts"] if prompt_files.pending(root) else []),
        "scenes": sorted(scene for scene in running.scenes.keys() | saved.scenes.keys()
                         if running.scenes.get(scene) != saved.scenes.get(scene)),
        "personas": [{"path": path, "name": name} for path, name in sorted(changed_personas.items())],
    }


def _prepare(root: Path, edit: Callable[[dict, HostConfig], None]
             ) -> tuple[Path, Path, HostConfig]:
    from ..chat.tools import build_tools

    path, original = _read_root(root)
    saved = _load_host_source(path, copy.deepcopy(original))
    edit(original, saved)
    candidate = _load_host_source(path, copy.deepcopy(original))
    personas = {path: load_persona(path)
                for path in dict.fromkeys(settings.persona for settings in candidate.scenes.values())}
    for scene, settings in candidate.scenes.items():
        build_tools(candidate.scene_config(scene), personas[settings.persona],
                    platform=candidate.delivery == "onebot", host_management=True)
        if candidate.worker is not None:
            select_skills(scene_skill_catalog(candidate, scene),
                          personas[settings.persona].skills)

    descriptor, name = tempfile.mkstemp(prefix=".lenbot-config-", suffix=".json", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(original, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return path, temporary, candidate


def _mind_binding(config: HostConfig) -> tuple[str, str, str]:
    binding = config.models.roles.mind
    provider = config.models.providers[binding.provider]
    return provider.api, provider.base_url, binding.model


def _read_saved(root: Path) -> HostConfig:
    path, source = _read_root(root)
    return _load_host_source(path, source)


def save_config(root: Path, running: HostConfig, edit: Callable[[dict, HostConfig], None]) -> HostConfig:
    """Caller holds the host configuration write lock; only the validated root file is replaced."""
    path, temporary, candidate = _prepare(root, edit)
    try:
        if _mind_binding(candidate) != _mind_binding(running):
            raise ValueError(
                "运行中不能保存聊天模型的协议、地址或模型变更；请先停机，再显式转换可移植历史。根配置未保存"
            )
        if running.worker is not None and candidate.worker is not None:
            if any(getattr(running.worker, key) != getattr(candidate.worker, key)
                   for key in ("docker_host", "workspace_root", "runtime_root", "storage_pool")):
                raise ValueError(
                    "运行中不能保存任务 Docker 地址、工作区、运行目录或存储池的迁移；"
                    "先停机清理任务容器，再搬迁原文件和修改根配置。根配置未保存"
                )
        temporary.replace(path)
        sync_directory(path.parent)
        return candidate
    finally:
        temporary.unlink(missing_ok=True)
