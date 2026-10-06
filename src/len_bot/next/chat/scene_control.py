"""Typed scene-local conversation controls, separate from persistent configuration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..runtime.identity import roles_for

if TYPE_CHECKING:
    from ..config import LabConfig
    from ..storage.store import Store


@dataclass(frozen=True)
class TemporaryQuiet:
    started: float
    until: float
    direct: Literal['allow', 'defer']
    requester: str | None


class SceneControlArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    action: Literal['status', 'quiet', 'resume']
    requester: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_-]*:[^:\s/\\]+$')
    seconds: int | None = Field(default=None, ge=1, le=604800)
    direct: Literal['allow', 'defer'] | None = None

    @model_validator(mode='after')
    def action_fields(self):
        if self.action == 'quiet' and self.seconds is None:
            raise ValueError('quiet requires seconds')
        if self.action != 'quiet' and (self.seconds is not None or self.direct is not None):
            raise ValueError('seconds/direct are only accepted for quiet')
        if self.action != 'status' and self.requester is None:
            raise ValueError('quiet/resume require the actual requester account')
        return self


def require_control(store: Store, config: LabConfig, requester: str) -> None:
    if requester == config.bot_id or requester in config.permissions.blacklist:
        raise PermissionError('该账号不能调整本场景的临时参与状态')
    identities = roles_for(requester, owners=config.owners, scoped_owner=None,
                           admins=config.permissions.admins, whitelist=config.permissions.whitelist,
                           group_role=store.latest_sender_role(config.scene, requester)
                           if config.scene.split(':', 2)[1] == 'group' else None)
    if not identities.intersection(config.chat_control_roles):
        raise PermissionError('该账号没有本场景聊天控制权限')


SCENE_CONTROL_TOOL = {'type': 'function', 'function': {
    'name': 'scene_control',
    'description': '查看当前场景参与设置(status)，或按实际请求人账号临时安静(quiet)/恢复(resume)；打开或关闭整个群的聊天、操作其他群用 host_manage。'
        'quiet须给seconds（最多7天），direct默认allow仍回应本场景直接消息（群内@/回复，私聊本会话全部消息）；defer连直接消息也延后，须面板提前恢复。'
        '当前轮可收尾确认；暂停其他大脑参与及系统唤醒，不取消任务或阻止插件固定命令回复。'
        'resume只结束临时安静，不撤销根配置安静时段。持久设置通过已开放的 host_manage 或面板保存。',
    'parameters': SceneControlArguments.model_json_schema(),
}}
