"""Compact conversation-management arguments; settings schemas are disclosed on demand."""

from typing import Literal

from pydantic import BaseModel, Field, JsonValue, model_validator

from ..configuration.types import STRICT


Section = Literal['chat', 'scene', 'learning', 'schedules', 'tasks', 'memory', 'models',
                  'web_read', 'web_search', 'worker', 'limits', 'retention', 'plugin', 'scene_plugins']
ModelRole = Literal['mind', 'vision', 'memory', 'learner', 'worker', 'asr']


class HostManageArguments(BaseModel):
    model_config = STRICT
    action: Literal['status', 'describe', 'update', 'chat', 'restart']
    requester: str = Field(pattern=r'^[a-z][a-z0-9_-]*:[^:\s/\\]+$', description='实际发出管理请求的人的账号')
    section: Section | None = None
    scene: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_-]*:(group|private):[^:\s/\\]+$',
                              description='场景设置和 chat 默认当前会话；status 可按群查看。restart 始终重启整个宿主')
    plugin: str | None = None
    role: ModelRole | None = None
    changes: dict[str, JsonValue] | None = Field(default=None,
        description='update 的修改字段；可空设置块可用 null 关闭，字段和类型先通过 describe 读取')
    enabled: bool | None = Field(default=None,
        description='chat 的目标状态：true 打开该群聊天，false 关闭（关闭后只记录消息，Bot 和插件都不在该群发言）')

    @model_validator(mode='after')
    def action_fields(self):
        if self.action in {'describe', 'update'}:
            if self.section is None:
                raise ValueError('describe/update require section')
            if self.section == 'plugin' and self.plugin is None:
                raise ValueError('plugin section requires plugin')
            if self.section == 'models' and self.role is None:
                raise ValueError('models section requires role')
        elif self.section is not None or self.plugin is not None or self.role is not None:
            raise ValueError('status/chat/restart do not accept a settings target')
        if self.plugin is not None and self.section != 'plugin':
            raise ValueError('plugin is only used by the plugin section')
        if self.role is not None and self.section != 'models':
            raise ValueError('role is only used by the models section')
        if self.action == 'update':
            if 'changes' not in self.model_fields_set:
                raise ValueError('update requires changes')
        elif 'changes' in self.model_fields_set:
            raise ValueError('changes is only used by update')
        if self.action == 'chat':
            if self.enabled is None:
                raise ValueError('chat requires enabled')
        elif self.enabled is not None:
            raise ValueError('enabled is only used by chat')
        return self


HOST_MANAGE_TOOL = {'type': 'function', 'function': {
    'name': 'host_manage',
    'description': '管理员查看和修改宿主：各群名称与聊天开关、插件启停与参数、场景设置、模型参数、重启；'
        'status 列出各群（群名、聊天开关）、插件和可管理设置块，describe 按块读取字段说明与当前值，'
        'update 保存指定修改，chat 打开或关闭某个群的聊天，restart 按明确请求在当前轮收尾后重启。'
        '请求人使用原话中的真实账号。',
    'parameters': HostManageArguments.model_json_schema(),
}}
