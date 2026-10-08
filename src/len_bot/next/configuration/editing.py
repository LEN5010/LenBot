"""Shared root-file reads, validated edits and pending restart differences."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from ..config import HostConfig, _load_host_source, _read_root
from ..persona.profile import Persona, load_persona
from ..tools.skills import select_skills
from ..plugins.manifest import scene_skill_catalog
from ..plugins.install import PluginInstaller
from .. import prompt_files
from ..storage.files import sync_directory
from ..storage.sqlite import connect


def index_maintenance(config: HostConfig) -> list[dict]:
    """Read the existing vector bindings; rebuilding remains an explicit stopped-instance operation."""
    notes = []

    def changed(stored, binding) -> bool:
        provider = config.models.providers[binding.provider]
        return (stored['base_url'] != provider.base_url or stored['model'] != binding.model
                or (binding.dimensions is not None and stored['dimensions'] != binding.dimensions))

    memory = config.memory
    if memory is not None:
        index = memory.local.directory / '.memory-index.sqlite3'
        if index.is_file():
            with closing(connect(index, readonly=True)) as db:
                db.row_factory = sqlite3.Row
                stored = db.execute('SELECT base_url,model,dimensions FROM memory_vector_binding WHERE id=1').fetchone()
                binding = memory.local.embedding
                needs = (stored is not None if binding is None else
                         changed(stored, binding) if stored is not None else
                         db.execute('SELECT 1 FROM memory_files LIMIT 1').fetchone() is not None)
            if needs:
                notes.append({'component': '记忆向量', 'message': '记忆索引与保存的向量配置不同，普通重启不会重建索引。',
                              'command': 'python -m len_bot.next.maintenance.memory_reindex'})
    if config.database.is_file():
        with closing(connect(config.database, readonly=True)) as db:
            for scene, settings in config.scenes.items():
                if settings.learning is None or settings.learning.embedding is None:
                    continue
                binding = settings.learning.embedding
                for raw, dimensions in db.execute(
                        "SELECT vector_binding,vector_dimensions FROM expressions WHERE scene=? AND status='adopted'", (scene,)):
                    stored = None if raw is None else {**json.loads(raw), 'dimensions': dimensions}
                    if stored is None or changed(stored, binding):
                        notes.append({'component': f'{scene} 学习向量', 'message': '已采纳说法的向量与保存配置不同，需要停机重建。',
                                      'command': 'python -m len_bot.next.maintenance.reindex_expressions'})
                        break
    return notes


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
        "maintenance": index_maintenance(saved),
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


def _read_saved(root: Path) -> HostConfig:
    path, source = _read_root(root)
    return _load_host_source(path, source)


def save_config(root: Path, running: HostConfig, edit: Callable[[dict, HostConfig], None]) -> HostConfig:
    """Caller holds the host configuration write lock; only the validated root file is replaced."""
    path, temporary, candidate = _prepare(root, edit)
    try:
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
