"""Daily settings and explicit restart for the existing host conversation tools."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, TypeAdapter, create_model

from ..chat.host_manage import HostManageArguments
from ..config import HostConfig
from ..configuration.chat import SceneSettings, ScheduleSettings, WebReadSettings
from ..configuration.editing import _read_saved, restart_summary, save_config
from ..configuration.learning import LearningSettings
from ..configuration.models import Binding
from ..configuration.tasks import TaskSettings, WorkerSettings
from ..configuration.types import STRICT
from ..memory.service import LocalMemoryConfig
from ..models.asr import ASRBinding
from ..models.limits import ResourceLimits, apply_speech_limits
from ..plugins.manager import PluginManager, plugin_manifest
from ..plugins.manifest import Manifest, config_model, redact_values
from ..tools.discovery import model_schema
from ..tools.web_search import WebSearchSettings
from .lifecycle import HostLifecycle
from .retention import RetentionSettings

if TYPE_CHECKING:
    from .network import NetworkRuntime


SECTIONS = {
    'chat': '全局聊天、上下文、图片与语音处理',
    'scene': '场景参与、安静时段、主动话题与人格绑定',
    'learning': '场景表达、黑话、表情与回复效果学习',
    'schedules': '场景提醒开关与额度',
    'tasks': '场景任务开关与额度',
    'memory': '本地记忆召回、后台整理与摘要参数',
    'models': '已配置模型用途的生成参数',
    'web_read': '公开网页读取',
    'web_search': '公开网页搜索',
    'worker': '已配置任务执行器的资源与能力参数',
    'limits': '发言与模型预算',
    'retention': '记录保留策略',
    'plugin': '已安装插件的非秘密参数与启停',
    'scene_plugins': '场景启用的已配置插件',
}
SCENE_SECTIONS = {'scene', 'learning', 'schedules', 'tasks', 'scene_plugins'}
NULLABLE = {'learning', 'memory', 'web_read', 'web_search', 'retention'}


def _fields(model: type[BaseModel], names: tuple[str, ...]) -> type[BaseModel]:
    return create_model(model.__name__ + 'Managed', __config__=STRICT, **{
        name: (model.model_fields[name].annotation, deepcopy(model.model_fields[name])) for name in names
    })


CHAT = _fields(HostConfig, ('timezone', 'max_steps', 'turn_timeout_seconds', 'max_model_requests',
                           'text_delivery', 'compaction', 'images', 'audio'))
SCENE = _fields(SceneSettings, ('persona', 'timezone', 'persona_aliases', 'relationships',
                               'behavior_addendum', 'attention', 'proactive', 'transcribe_audio'))
LEARNING = _fields(LearningSettings, tuple(name for name in LearningSettings.model_fields if name != 'embedding'))
SCHEDULES = _fields(ScheduleSettings, ('enabled', 'max_pending', 'autonomous'))
TASKS = _fields(TaskSettings, ('enabled', 'max_running', 'max_daily_tasks', 'egress_max_task_bytes',
                             'egress_max_daily_bytes', 'egress_bytes_per_second'))
MEMORY = _fields(LocalMemoryConfig, ('auto_recall', 'recall_budget_chars', 'recall_limit', 'ingest', 'summaries'))
MODEL = _fields(Binding, ('context_window_tokens', 'temperature', 'max_output_tokens',
                         'timeout_seconds', 'reasoning_effort'))
ASR = _fields(ASRBinding, ('timeout_seconds', 'language'))
WORKER = _fields(WorkerSettings, tuple(name for name in WorkerSettings.model_fields if name not in {
    'docker_binary', 'docker_host', 'image', 'workspace_root', 'runtime_root', 'delivery_root',
    'storage_pool', 'skills_directory', 'uid', 'gid',
}))
SCENE_PLUGINS = _fields(SceneSettings, ('plugins',))
SECTION_MODELS = {'chat': CHAT, 'scene': SCENE, 'learning': LEARNING, 'schedules': SCHEDULES,
                  'tasks': TASKS, 'memory': MEMORY, 'worker': WORKER, 'scene_plugins': SCENE_PLUGINS,
                  'web_read': WebReadSettings, 'web_search': WebSearchSettings,
                  'limits': ResourceLimits, 'retention': RetentionSettings}
BOOL = TypeAdapter(bool, config=STRICT)


def require_management(config: HostConfig, scene: str, requester: str) -> None:
    identities = config.scene_config(scene).permissions
    if requester == config.bot_id or requester in identities.blacklist:
        raise PermissionError('该账号不能管理机器人配置或重启')
    if requester not in config.owners and requester not in config.permissions.admins:
        raise PermissionError('只有机器人主人和全局配置管理员可以管理配置或重启')


def _merge(original: dict, changes: dict) -> dict:
    result = deepcopy(original)
    for key, value in changes.items():
        result[key] = (_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict)
                       else deepcopy(value))
    return result


def _public_fields(manifest: Manifest) -> dict:
    return {name: item for name, item in manifest.config.items()
            if item.type != 'secret' and not any(child.type == 'secret' for child in item.fields.values())}


class HostManagement:
    def __init__(self, root: Path, runtime: NetworkRuntime, lifecycle: HostLifecycle):
        self.root, self.runtime, self.lifecycle = root, runtime, lifecycle
        self.running = runtime.config
        self.write_lock = runtime.config_write_lock
        self.plugins = PluginManager(root, runtime, self.running, self.write_lock)

    def _pending(self) -> dict:
        return restart_summary(self.root, self.running,
                               {scene: chat.persona for scene, chat in self.runtime.chats.items()})

    def _path(self, args: HostManageArguments, scene: str, saved: HostConfig) -> tuple[str, ...]:
        section = args.section
        if section in SCENE_SECTIONS:
            target = scene if args.scene is None else args.scene
            if target not in saved.scenes:
                raise ValueError(f'保存配置中没有场景 {target}')
            return ('scenes', target) if section in {'scene', 'scene_plugins'} else ('scenes', target, section)
        if args.scene is not None:
            raise ValueError('此设置块不接受 scene；场景设置请选择场景设置块')
        if section == 'chat':
            return ()
        if section == 'models':
            return ('models', 'roles', args.role)
        return (section,)

    def _model(self, args: HostManageArguments) -> type[BaseModel]:
        if args.section == 'models':
            return ASR if args.role == 'asr' else MODEL
        return SECTION_MODELS[args.section]

    def _value(self, config: HostConfig, path: tuple[str, ...], args: HostManageArguments) -> dict | None:
        value = config
        if path and path[0] == 'scenes' and path[1] not in config.scenes:
            return None
        for name in path:
            value = value[name] if isinstance(value, dict) else getattr(value, name)
        if value is None:
            return None
        return value.model_dump(mode='json', include=set(self._model(args).model_fields))

    def _schema(self, args: HostManageArguments) -> dict:
        schema = self._model(args).model_json_schema()
        if args.section in NULLABLE:
            definitions = schema.pop('$defs', {})
            schema = {'anyOf': [schema, {'type': 'null'}], '$defs': definitions}
        return model_schema(schema)

    def _describe(self, saved: HostConfig, path: tuple[str, ...], args: HostManageArguments) -> dict:
        current, recorded = self._value(self.running, path, args), self._value(saved, path, args)
        result = {'section': args.section, 'target': list(path), 'description': SECTIONS[args.section],
                  'schema': self._schema(args), 'running': current, 'saved': recorded,
                  'restart_required': current != recorded,
                  'applies': 'speech_now_tokens_restart' if args.section == 'limits' else 'restart'}
        if args.section == 'scene':
            result['personas'] = sorted({str(settings.persona) for settings in saved.scenes.values()})
        if args.section in {'memory', 'worker', 'models'}:
            result['setup'] = '只调整已有配置的参数；目录、凭据及模型改绑由面板或离线维护处理'
        return result

    def _plugin_state(self, config: HostConfig, name: str, manifest: Manifest) -> dict | None:
        if config.plugins is None or name not in config.plugins.configured:
            return None
        return {'enabled': name not in config.plugins.disabled,
                'config': {key: value for key, value in config.plugins.configured[name].items()
                           if key in _public_fields(manifest)}}

    def _plugin_description(self, saved: HostConfig, args: HostManageArguments) -> dict:
        name = args.plugin
        if saved.plugins is None or name not in saved.plugins.configured:
            raise ValueError(f'插件 {name} 尚未配置；安装和首次配置请使用面板')
        manifest = plugin_manifest(saved, name)
        values = config_model('ManagedPluginValues', _public_fields(manifest), saved.scenes)
        schema = create_model('ManagedPlugin', __config__=STRICT,
                              enabled=(bool, ...), config=(values, ...)).model_json_schema()
        current, recorded = self._plugin_state(self.running, name, manifest), self._plugin_state(saved, name, manifest)
        return {'section': 'plugin', 'plugin': name, 'description': manifest.description,
                'schema': model_schema(schema), 'running': current, 'saved': recorded,
                'restart_required': current != recorded, 'applies': 'plugin_reload'}

    async def execute(self, scene: str, args: HostManageArguments) -> dict:
        require_management(self.running, scene, args.requester)
        if args.action in {'status', 'restart'}:
            async with self.write_lock:
                scenes = list(self.running.scenes)
                if args.scene is not None:
                    if args.scene not in self.running.scenes:
                        raise ValueError(f'运行配置中没有场景 {args.scene}')
                    scenes = [args.scene]
                pending = await asyncio.to_thread(self._pending)
                if args.action == 'restart':
                    self.lifecycle.require_restart()
                    return {'accepted': True, 'scope': 'host', 'restart_after': 'current_turn', 'pending': pending}
                titles, title_errors = await self.runtime.scene_titles.read()
                scene_rows = [{'scene': name, 'title': titles.get(name),
                               'chat': 'off' if self.runtime.runners[name].state.paused else 'on',
                               **({'title_error': title_errors[name]} if name in title_errors else {})}
                              for name in scenes]
                return {'sections': SECTIONS, 'scenes': scene_rows,
                        'plugins': [] if self.running.plugins is None else list(self.running.plugins.configured),
                        'model_roles': [role for role in ('mind', 'vision', 'memory', 'learner', 'worker', 'asr')
                                        if getattr(self.running.models.roles, role) is not None],
                        'pending': pending, 'restartable': self.lifecycle.restartable and self.lifecycle.shutdown is not None}
        if args.action == 'chat':
            return await self._chat(scene, args)
        if args.section == 'plugin':
            if args.scene is not None:
                raise ValueError('插件全局参数不接受 scene；选群请使用 scene_plugins')
            if args.action == 'update':
                return await self._update_plugin(args)
            async with self.write_lock:
                saved = await asyncio.to_thread(_read_saved, self.root)
                return await asyncio.to_thread(self._plugin_description, saved, args)
        if args.action == 'describe':
            async with self.write_lock:
                saved = await asyncio.to_thread(_read_saved, self.root)
                path = self._path(args, scene, saved)
                return self._describe(saved, path, args)
        if args.section == 'scene_plugins':
            async with self.plugins.lock:
                return await self._update(scene, args)
        return await self._update(scene, args)

    async def _update(self, scene: str, args: HostManageArguments) -> dict:
        changes, names = args.changes, []
        before = None
        path = ()

        def edit(source: dict, saved: HostConfig) -> None:
            nonlocal before, path
            path = self._path(args, scene, saved)
            before = self._value(saved, path, args)
            if changes is None:
                if args.section not in NULLABLE:
                    raise ValueError(f'{args.section} 不能通过 null 关闭')
            else:
                if args.section in {'memory', 'worker', 'models'} and before is None:
                    raise ValueError('此能力尚未配置；请先在面板设置实际目录或模型绑定')
                allowed = set(self._model(args).model_fields)
                unknown = set(changes) - allowed
                if unknown:
                    raise ValueError(f'{args.section} 没有可写字段 {sorted(unknown)}；请先 describe')
            target = source
            for key in path[:-1]:
                target = target[key]
            if not path:
                target.update(_merge({key: source[key] for key in changes if key in source}, changes))
            else:
                key = path[-1]
                target[key] = None if changes is None else _merge(target.get(key) or {}, changes)
            if args.section == 'scene_plugins' and path[1] in self.running.scenes:
                selected = set(target[path[-1]]['plugins'])
                names.extend(sorted((set(saved.scenes[path[1]].plugins) ^ selected)
                                    | (set(self.running.scenes[path[1]].plugins) ^ selected)))

        async with self.write_lock:
            saved = await asyncio.to_thread(save_config, self.root, self.running, edit)
            if args.section == 'limits':
                apply_speech_limits(self.running, saved)
                for runner in self.runtime.runners.values():
                    runner.changed.set()
            result = self._describe(saved, path, args)
        result.update(before=before, saved_to='lenbot.config.json')
        if names:
            await self._apply_plugins(names, saved)
            result = {**result, **self._describe(saved, path, args), 'applies': 'plugin_reload', 'applied': True}
        result.pop('schema')
        self.runtime.notify()
        return result

    async def _update_plugin(self, args: HostManageArguments) -> dict:
        name, changes = args.plugin, args.changes
        if changes is None:
            raise ValueError('插件启停使用 enabled，不使用 null')
        unknown = set(changes) - {'enabled', 'config'}
        if unknown:
            raise ValueError(f'插件没有管理字段 {sorted(unknown)}；请先 describe')
        before = None

        def edit(source: dict, saved: HostConfig) -> None:
            nonlocal before
            before = self._plugin_description(saved, args)['saved']
            manifest = plugin_manifest(saved, name)
            plugins = source['plugins']
            if 'config' in changes:
                patch = TypeAdapter(dict[str, object], config=STRICT).validate_python(changes['config'])
                unknown = set(patch) - _public_fields(manifest).keys()
                if unknown:
                    raise ValueError(f'插件 {name} 没有非秘密可写字段 {sorted(unknown)}')
                plugins[name] = _merge(plugins[name], patch)
            if 'enabled' in changes:
                enabled = BOOL.validate_python(changes['enabled'])
                disabled = [item for item in plugins.get('disabled', []) if item != name]
                if not enabled:
                    disabled.append(name)
                plugins['disabled'] = disabled
            if 'config' in changes or changes.get('enabled') is True:
                try:
                    manifest.values_model(saved.scenes).model_validate(plugins[name])
                except ValueError as error:
                    raise ValueError(redact_values(str(error), manifest, plugins[name])) from None

        async with self.plugins.lock:
            saved = await self.plugins.save(edit)
            await self._apply_plugins([name], saved)
            result = self._plugin_description(saved, args)
        result.pop('schema')
        self.runtime.notify()
        return {**result, 'before': before, 'saved_to': 'lenbot.config.json', 'applied': True}

    async def _apply_plugins(self, names: list[str], saved: HostConfig) -> None:
        for name in names:
            try:
                await self.plugins.apply(name, saved)
            except Exception as error:
                raise RuntimeError(f'配置已保存；插件 {name} 应用失败：{type(error).__name__}: {error}') from error

    async def _chat(self, scene: str, args: HostManageArguments) -> dict:
        target = scene if args.scene is None else args.scene
        if target not in self.runtime.runners:
            raise ValueError(f'运行中没有场景 {target}')
        if not self.runtime.accepting:
            raise RuntimeError('宿主没有在运行，聊天开关未改变')
        runner = self.runtime.runners[target]
        titles, _ = await self.runtime.scene_titles.read()
        result = {'scene': target, 'title': titles.get(target)}
        if target == scene:
            # This turn holds the scene's execution and saves its attention state at the end,
            # so the switch for the current scene is written right after the turn.
            if args.enabled:
                return {**result, 'chat': 'on', 'changed': False}
            return {**result, 'chat': 'off', 'changed': True, 'applies': 'after_current_turn'}
        # Bound the wait: another scene's turn may be waiting for this scene's execution.
        try:
            async with asyncio.timeout(1):
                async with runner.execution:
                    before = runner.state.paused
                    state = runner.set_paused(not args.enabled)
        except TimeoutError as error:
            raise RuntimeError(f'目标群 {target} 正在执行，1 秒内未取得场景锁，聊天开关未改变') from error
        return {**result, 'chat': 'off' if state['paused'] else 'on', 'changed': before != state['paused']}

    async def finish_turn(self) -> None:
        await self.lifecycle.restart()
