"""Edit real identity lists and existing per-capability role settings together."""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, field_validator

from .identity import IdentitySettings
from .tasks_config import TaskRole
from .host_settings import _body, _prepare, _read_saved


class CapabilityMatrix(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    delegate: list[TaskRole]
    task_manage: list[TaskRole]
    long_running: list[TaskRole]
    own_reminder: list[TaskRole]
    other_reminder: list[TaskRole]
    reminder_manage: list[TaskRole]

    @field_validator('*')
    @classmethod
    def distinct(cls, value):
        if len(set(value)) != len(value):
            raise ValueError('roles must not repeat')
        return value


class PermissionChange(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    global_identities: IdentitySettings
    scene_identities: IdentitySettings | None
    matrix: CapabilityMatrix


def matrix(config, scene: str) -> dict:
    item = config.scenes[scene]
    return {'delegate': item.tasks.delegate_roles, 'task_manage': item.tasks.manage_roles,
            'long_running': item.tasks.long_running_roles, 'own_reminder': item.schedules.own,
            'other_reminder': item.schedules.others, 'reminder_manage': item.schedules.manage}


def register_host_permissions(app: FastAPI, *, root: Path, runtime, user, write_lock):
    def section(config, scene):
        local = config.scenes[scene].permissions
        return {'global_identities': config.permissions.model_dump(),
                'scene_identities': None if local is None else local.model_dump(), 'matrix': matrix(config, scene)}

    def snapshot(saved, scene):
        if scene not in saved.scenes or scene not in runtime.config.scenes:
            raise HTTPException(404, '请选当前运行与保存配置中都存在的场景')
        current, recorded = section(runtime.config, scene), section(saved, scene)
        item = runtime.config.scenes[scene]
        return {'running': current, 'saved': recorded, 'restart_required': current != recorded,
                'owner_qq': runtime.config.owner_qq,
                'effective_identities': runtime.config.scene_config(scene).permissions.model_dump(),
                'scoped_identities': {'tasks': {'owner': item.tasks.owner, 'admins': item.tasks.admins, 'whitelist': item.tasks.whitelist},
                                      'schedules': {'owner': item.schedules.owner, 'admins': item.schedules.admins, 'whitelist': item.schedules.whitelist}}}

    @app.get('/api/host/permissions')
    async def state(scene: str, _: str = Depends(user)):
        try:
            return snapshot(await asyncio.to_thread(_read_saved, root), scene)
        except (ValueError, OSError) as error:
            raise HTTPException(422, str(error)) from error

    @app.put('/api/host/permissions')
    async def save(scene: str, request: Request, _: str = Depends(user)):
        change = await _body(request, PermissionChange)
        if scene not in runtime.config.scenes:
            raise HTTPException(404, '当前宿主未配置此场景')
        def edit(source, saved):
            if scene not in saved.scenes:
                raise ValueError('保存配置中没有该场景')
            source['permissions'] = change.global_identities.model_dump()
            local = source['scenes'][scene]
            local['permissions'] = None if change.scene_identities is None else change.scene_identities.model_dump()
            local.setdefault('tasks', {}).update(delegate_roles=change.matrix.delegate,
                manage_roles=change.matrix.task_manage, long_running_roles=change.matrix.long_running)
            local.setdefault('schedules', {}).update(own=change.matrix.own_reminder,
                others=change.matrix.other_reminder, manage=change.matrix.reminder_manage)
        async with write_lock:
            try:
                path, temporary, candidate = await asyncio.to_thread(_prepare, root, edit)
                try:
                    await asyncio.to_thread(temporary.replace, path)
                finally:
                    temporary.unlink(missing_ok=True)
                return snapshot(candidate, scene)
            except (ValueError, OSError) as error:
                raise HTTPException(422, str(error)) from error
