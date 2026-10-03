"""Operator access to real skill files and explicit directory moves/deletions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator

from .config import HostConfig, load_host_config
from .config_types import STRICT
from .host_capabilities import skill_info
from .network import NetworkRuntime
from .persona import load_persona
from .skill_files import delete_skill, list_files, move_skill, read_file
from .skills import BUILTIN_DIRECTORY, load_skill, load_task_skills, select_skills
from .plugin_manifest import scene_skill_catalog
from .tasks_store import TERMINAL, Task, TaskStore


Source = Literal['builtin', 'shared', 'scene', 'task', 'plugin']
SkillName = Annotated[str, Field(min_length=1, max_length=64, pattern=r'^[a-z0-9]+(?:-[a-z0-9]+)*$')]


class MoveSkill(BaseModel):
    model_config = STRICT
    source: Literal['task', 'scene']
    name: SkillName
    task_id: int | None = Field(default=None, ge=1)
    target: Literal['scene', 'shared']

    @model_validator(mode='after')
    def source_and_target(self) -> MoveSkill:
        if (self.source == 'task') != (self.task_id is not None):
            raise ValueError('task 来源必须指定 task_id，其他来源不接受 task_id')
        if self.source == 'scene' and self.target != 'shared':
            raise ValueError('本场景技能只能提升到共享目录')
        return self


def _failure(error: ValueError | OSError) -> HTTPException:
    code = (404 if isinstance(error, FileNotFoundError)
            else 409 if isinstance(error, (ValueError, FileExistsError)) else 500)
    return HTTPException(code, f'{type(error).__name__}: {error}')


def register_host_skills(app: FastAPI, *, root: Path, runtime: NetworkRuntime,
                         user: Callable[[Request], str], write_lock: asyncio.Lock) -> None:
    records = TaskStore(runtime.store)

    def scene_exists(scene: str) -> None:
        if scene not in runtime.chats:
            raise HTTPException(404, '当前宿主未配置这一场景')

    def stopped(scene: str, task_id: int) -> Task:
        try:
            item = records.get(scene, task_id)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error
        if item.status not in TERMINAL or item.container is not None:
            raise HTTPException(409, '先结束任务并停止其容器，再读取或采用任务技能')
        if records.workspace_discarded(scene, task_id):
            raise HTTPException(409, '本任务环境已被明确放弃，不再从其旧工作区读取或采用技能')
        return item

    def task_workspace(scene: str, task_id: int) -> Path:
        worker = runtime.config.worker
        if worker is None:
            raise HTTPException(409, '当前运行配置没有 worker，无法定位原任务工作区')
        return worker.workspace_root / scene / 'tasks' / str(task_id)

    def skill_directory(config: HostConfig) -> Path:
        if config.worker is None or config.worker.skills_directory is None:
            raise HTTPException(409, '先在根配置中设置 worker.skills_directory')
        return config.worker.skills_directory

    def location(config: HostConfig, scene: str, source: Source, name: str,
                 task_id: int | None) -> tuple[Path, str, Task | None]:
        if source == 'task':
            if task_id is None:
                raise HTTPException(422, 'task 来源需要 task_id')
            item = stopped(scene, task_id)
            return task_workspace(scene, task_id) / 'skills' / name, f'/workspace/skills/{name}', item
        if task_id is not None:
            raise HTTPException(422, '非任务来源不接受 task_id')
        if source == 'builtin':
            return BUILTIN_DIRECTORY / name, f'/shared/skills/builtin/{name}', None
        if source == 'plugin':
            for skill in scene_skill_catalog(config, scene):
                if skill.source == 'plugin' and skill.name == name:
                    return skill.host_path, skill.container_path, None
            raise HTTPException(404, '当前场景没有这个插件技能')
        directory = skill_directory(config)
        if source == 'shared':
            return directory / 'shared' / name, f'/shared/skills/approved/{name}', None
        return directory / 'scenes' / scene / name, f'/group/skills/{name}', None

    def unchanged_task(previous: Task | None) -> None:
        if previous is not None:
            current = stopped(previous.scene, previous.id)
            if (current.started, current.ended) != (previous.started, previous.ended):
                raise HTTPException(409, '读取期间任务已被继续，请重读本次实际技能文件')

    def running_users(path: Path) -> list[str]:
        return [scene for scene, chat in runtime.chats.items()
                if any(skill.host_path == path for skill in chat.skills)]

    def not_in_use(path: Path) -> None:
        scenes = running_users(path)
        if scenes:
            raise HTTPException(409, f'这些场景的运行快照仍引用此目录：{scenes}；先取消该项许可并重启，再移动或删除')

    def affected(config: HostConfig, scene: str, source: str) -> list[str]:
        if source != 'shared' and scene not in config.scenes:
            raise HTTPException(409, '此场景已从保存配置移除，不能修改其场景技能')
        return list(config.scenes) if source == 'shared' else [scene]

    def saved_references(config: HostConfig, scenes: list[str], name: str) -> list[str]:
        return [scene for scene in scenes
                if isinstance((permission := load_persona(config.scenes[scene].persona).skills), list)
                and name in permission]

    @app.get('/api/host/tasks/{id}/skills')
    async def candidates(id: int, scene: str, _: str = Depends(user)):
        scene_exists(scene)
        async with write_lock:
            item = stopped(scene, id)
            try:
                skills = await asyncio.to_thread(load_task_skills, task_workspace(scene, id))
                unchanged_task(item)
                return {'task_id': id, 'scene': scene, 'items': [skill_info(skill) for skill in skills],
                        'notice': '来自该任务实际 skills 目录；不是已采用或已执行记录。采用将移动原目录。'}
            except (ValueError, OSError) as error:
                raise _failure(error) from error

    @app.get('/api/host/skills/files')
    async def files(scene: str, source: Source, name: SkillName,
                    task_id: int | None = Query(None, ge=1), _: str = Depends(user)):
        scene_exists(scene)
        async with write_lock:
            try:
                config = await asyncio.to_thread(load_host_config, root)
                path, container, item = location(config, scene, source, name, task_id)

                def inspect():
                    entries = list_files(path)
                    skill = load_skill(path, source, container)
                    refs = ([] if source in {'builtin', 'task', 'plugin'} else
                            saved_references(config, affected(config, scene, source), name))
                    return skill, entries, refs

                skill, entries, refs = await asyncio.to_thread(inspect)
                unchanged_task(item)
                return {'skill': skill_info(skill), 'files': entries,
                        'used_by_running': running_users(path), 'referenced_by_saved': refs}
            except (ValueError, OSError) as error:
                raise _failure(error) from error

    @app.get('/api/host/skills/text')
    async def text_file(scene: str, source: Source, name: SkillName, path: str = 'SKILL.md',
                        offset: int = Query(0, ge=0), task_id: int | None = Query(None, ge=1),
                        _: str = Depends(user)):
        scene_exists(scene)
        async with write_lock:
            try:
                config = await asyncio.to_thread(load_host_config, root)
                directory, _, item = location(config, scene, source, name, task_id)
                result = await asyncio.to_thread(read_file, directory, path, offset)
                unchanged_task(item)
                return result
            except (ValueError, OSError) as error:
                raise _failure(error) from error

    @app.post('/api/host/skills/move')
    async def move(scene: str, body: MoveSkill, _: str = Depends(user)):
        scene_exists(scene)
        async with write_lock:
            try:
                config = await asyncio.to_thread(load_host_config, root)
                source, container, item = location(config, scene, body.source, body.name, body.task_id)
                destination, target_container, _ = location(config, scene, body.target, body.name, None)
                not_in_use(source)
                scenes = affected(config, scene, body.target)

                def prepare():
                    entries = list_files(source, independent=True)
                    original = load_skill(source, body.source, container)
                    target = replace(original, source=body.target, host_path=destination,
                                     container_path=target_container)
                    for key in scenes:
                        catalog = tuple(skill for skill in scene_skill_catalog(config, key)
                                        if skill.host_path != source)
                        if any(skill.name == target.name for skill in catalog):
                            raise ValueError(f'{key} 已存在同名技能 {target.name!r}，没有覆盖或移动')
                        select_skills((*catalog, target), load_persona(config.scenes[key].persona).skills)
                    return target, entries

                target, entries = await asyncio.to_thread(prepare)
                unchanged_task(item)
                # No await from the final task-state check to the filesystem
                # rename: a panel continuation cannot restart the source here.
                move_skill(source, destination)
                return {'skill': skill_info(target), 'moved_from': str(source), 'moved_to': str(destination),
                        'files': len(entries), 'bytes': sum(entry['size'] for entry in entries),
                        'affected_scenes': scenes, 'restart_required': True}
            except (ValueError, OSError) as error:
                raise _failure(error) from error

    @app.delete('/api/host/skills/{source}/{name}')
    async def delete(scene: str, source: Literal['scene', 'shared'], name: SkillName,
                     _: str = Depends(user)):
        scene_exists(scene)
        async with write_lock:
            try:
                config = await asyncio.to_thread(load_host_config, root)
                path, _, _ = location(config, scene, source, name, None)
                not_in_use(path)
                scenes = affected(config, scene, source)
                refs = await asyncio.to_thread(saved_references, config, scenes, name)
                if refs:
                    raise HTTPException(409, f'已保存角色仍按名称引用此技能：{refs}；先修改角色许可，没有删除')
                await asyncio.to_thread(list_files, path)
                await asyncio.to_thread(delete_skill, path)
                return {'deleted': str(path), 'name': name, 'source': source,
                        'affected_scenes': scenes}
            except (ValueError, OSError) as error:
                raise _failure(error) from error
