"""Task-oriented setup previews that write through the existing root lock."""
from __future__ import annotations

import uuid

from len_bot.runtime.capabilities import Capability, CapabilitySubject, first_allowing_grant


WIZARDS = ('python', 'research', 'broadcast')
BROADCAST_PLUGINS = ('asoul_calendar', 'asoul_dynamics', 'bilibili_live_sensor')
PYTHON_PLUGINS = ('workspace', 'python_workspace')

# A grant may not expire at or before the Unix epoch, so asking the same matcher
# at time 0 answers "would this grant still match if it had not lapsed?".
BEFORE_ANY_EXPIRY = 0.0


def _scene_id(value):
    if not isinstance(value, str) or not value.startswith('group:'):
        raise ValueError('必须选择一个真实群场景')
    return value


def _plugin_state(data, plugin_id):
    return data['plugins'].get(plugin_id) or {'enabled': False, 'config': None, 'credential_revision': 1}


def _backend(data):
    for name in PYTHON_PLUGINS:
        setting = data['plugins'].get(name)
        if not setting or not setting.get('enabled') or not setting.get('config'):
            continue
        config = setting['config']
        if config.get('gateway'):
            return name, 'gateway', '独立 Gateway'
        if config.get('worker'):
            return name, 'local', '本机试用 worker'
    return None, None, '未配置已启用的执行后端'


def _has_grant(grants, now, *, principal_type, principal_id, scene_id=None, system_scope=None, capability):
    """The runtime's own admission question, asked against the draft grant list.

    A lapsed or disabled grant is not "already granted", so the wizard offers to
    issue a working one instead of reporting a permission the runtime refuses.
    """
    subject = CapabilitySubject(principal_type, principal_id, scene_id, system_scope)
    return first_allowing_grant(grants, subject, Capability(capability), now)


def _grant_note(grants, now, base, **subject):
    """Say whether the proposed grant is the first one or replaces a lapsed one.

    The "lapsed" reading comes from the same matcher asked before any expiry, so
    the wizard never keeps a second opinion about who is authorised.
    """
    if _has_grant(grants, BEFORE_ANY_EXPIRY, **subject) is not None:
        return base + '；原有同主体授予已过有效期，按当前判定不再允许'
    return base


def preview_python(root, values):
    scene_id = _scene_id(values.get('scene_id'))
    data = root.model_dump()
    plugin_id, kind, label = _backend(data)
    changes = []
    blocked = None
    if plugin_id is None:
        blocked = '还没有已启用的 Python 执行后端。请先在插件页填写本机 worker 或 Gateway，不要在此向导里填写空 JSON。'
        return {'wizard': 'python', 'blocked': blocked, 'changes': changes, 'backend': label,
                'scene_id': scene_id, 'plugin_id': None}
    scene = data['scenes'].get(scene_id)
    if scene is None:
        blocked = '该群尚未出现在根配置的场景表中'
        return {'wizard': 'python', 'blocked': blocked, 'changes': changes, 'backend': label,
                'scene_id': scene_id, 'plugin_id': plugin_id}
    plugins = dict(scene.get('plugins') or {})
    current = plugins.get(plugin_id)
    if current is None:
        changes.append({'path': f'scenes.{scene_id}.plugins.{plugin_id}', 'from': None,
                        'to': {'enabled': True, 'config': {}}, 'note': '为本群建立空参数启用记录'})
    elif not current.get('enabled'):
        changes.append({'path': f'scenes.{scene_id}.plugins.{plugin_id}.enabled', 'from': False,
                        'to': True, 'note': '开放本群使用已有后端'})
    else:
        changes.append({'path': f'scenes.{scene_id}.plugins.{plugin_id}', 'from': current,
                        'to': current, 'note': '本群已经开放，无需改配置'})
    return {'wizard': 'python', 'blocked': None, 'changes': changes, 'backend': label,
            'backend_kind': kind, 'scene_id': scene_id, 'plugin_id': plugin_id,
            'note': '不配置出网，不授予文件发布。保存后用明确委托发起一次 Python 工作核对。'}


def apply_python(data, values):
    preview = preview_python(type('R', (), {'model_dump': lambda self: data})(), values)
    if preview['blocked']:
        raise ValueError(preview['blocked'])
    plugin_id = preview['plugin_id']
    scene_id = preview['scene_id']
    scene = data['scenes'][scene_id]
    plugins = dict(scene.get('plugins') or {})
    current = plugins.get(plugin_id) or {'enabled': False, 'config': {}}
    current = {**current, 'enabled': True, 'config': current.get('config') or {}}
    plugins[plugin_id] = current
    scene['plugins'] = plugins
    data['scenes'][scene_id] = scene
    return data


def preview_research(root, values, now):
    data = root.model_dump()
    topics = [item.strip() for item in (values.get('topics') or []) if isinstance(item, str) and item.strip()]
    if len(topics) > 20:
        raise ValueError('研究主题最多 20 项')
    share_scenes = [_scene_id(item) for item in (values.get('share_scenes') or [])]
    daily_limit = int(values.get('daily_limit') or 0)
    cooldown = int(values.get('cooldown_seconds') or 3600)
    if daily_limit < 0 or cooldown < 60:
        raise ValueError('分享次数不能为负，冷却至少 60 秒')
    changes = []
    runtime = data['runtime']
    if not runtime.get('heartbeat_enabled'):
        changes.append({'path': 'runtime.heartbeat_enabled', 'from': False, 'to': True, 'note': '打开系统研究周期'})
    if topics and topics != runtime.get('heartbeat_topics'):
        changes.append({'path': 'runtime.heartbeat_topics', 'from': runtime.get('heartbeat_topics') or [],
                        'to': topics, 'note': '写入研究主题'})
    grants = list(data['access']['capability_grants'])
    research = _has_grant(grants, now, principal_type='system', principal_id='scheduler',
                          system_scope='heartbeat', capability=Capability.PUBLIC_RESEARCH.value)
    if research is None:
        changes.append({'path': 'access.capability_grants', 'from': None,
                        'to': {'principal_type': 'system', 'principal_id': 'scheduler',
                               'system_scope': 'heartbeat', 'capabilities': [Capability.PUBLIC_RESEARCH.value]},
                        'note': _grant_note(grants, now, '为 scheduler/heartbeat 授予公共研究；不猜其他主体',
                                            principal_type='system', principal_id='scheduler',
                                            system_scope='heartbeat',
                                            capability=Capability.PUBLIC_RESEARCH.value)})
    share = _plugin_state(data, 'interest_share')
    if share_scenes and share.get('config') is None:
        return {'wizard': 'research', 'blocked': '分享前需要先在插件页填写 interest_share 全局参数',
                'changes': changes, 'share_scenes': share_scenes}
    if share_scenes and not share.get('enabled'):
        changes.append({'path': 'plugins.interest_share.enabled', 'from': False, 'to': True,
                        'note': '启用公共兴趣分享插件；研究成功仍不自动全群发布'})
    for scene_id in share_scenes:
        scene = data['scenes'].get(scene_id)
        if scene is None:
            changes.append({'path': f'scenes.{scene_id}', 'from': None, 'to': None, 'note': '该群不在场景表，无法写入分享'})
            continue
        current = (scene.get('plugins') or {}).get('interest_share')
        target = {'enabled': True, 'config': {'topics': [], 'daily_limit': daily_limit,
                                              'cooldown_seconds': cooldown}}
        changes.append({'path': f'scenes.{scene_id}.plugins.interest_share', 'from': current, 'to': target,
                        'note': '本群独立分享次数与冷却'})
        grant = _has_grant(grants, now, principal_type='plugin', principal_id='interest_share',
                           scene_id=scene_id, capability=Capability.INTEREST_SHARE.value)
        if grant is None:
            changes.append({'path': 'access.capability_grants', 'from': None,
                            'to': {'principal_type': 'plugin', 'principal_id': 'interest_share',
                                   'scene_id': scene_id, 'capabilities': [Capability.INTEREST_SHARE.value]},
                            'note': _grant_note(grants, now, '向本群插件主体授予分享',
                                                principal_type='plugin', principal_id='interest_share',
                                                scene_id=scene_id,
                                                capability=Capability.INTEREST_SHARE.value)})
    return {'wizard': 'research', 'blocked': None, 'changes': changes, 'share_scenes': share_scenes,
            'note': '没有分享群也可以只做研究。启用配置不会启动一次付费模型或发送预览。'}


def apply_research(data, values, now, *, operator_id):
    preview = preview_research(type('R', (), {'model_dump': lambda self: data})(), values, now)
    if preview['blocked']:
        raise ValueError(preview['blocked'])
    topics = [item.strip() for item in (values.get('topics') or []) if isinstance(item, str) and item.strip()]
    share_scenes = [_scene_id(item) for item in (values.get('share_scenes') or [])]
    daily_limit = int(values.get('daily_limit') or 0)
    cooldown = int(values.get('cooldown_seconds') or 3600)
    data['runtime']['heartbeat_enabled'] = True
    if topics:
        data['runtime']['heartbeat_topics'] = topics
    grants = list(data['access']['capability_grants'])
    if _has_grant(grants, now, principal_type='system', principal_id='scheduler',
                  system_scope='heartbeat', capability=Capability.PUBLIC_RESEARCH.value) is None:
        grants.append({
            'grant_id': 'g' + uuid.uuid4().hex, 'revision': 1, 'operator_id': operator_id, 'principal_type': 'system',
            'principal_id': 'scheduler', 'scene_id': None, 'system_scope': 'heartbeat',
            'capabilities': [Capability.PUBLIC_RESEARCH.value], 'expires_at': None,
            'resource_policy': None, 'concurrency': None, 'enabled': True,
        })
    if share_scenes:
        share = data['plugins'].setdefault('interest_share', {'enabled': False, 'config': None})
        share['enabled'] = True
        for scene_id in share_scenes:
            scene = data['scenes'][scene_id]
            plugins = dict(scene.get('plugins') or {})
            plugins['interest_share'] = {'enabled': True, 'config': {
                'topics': [], 'daily_limit': daily_limit, 'cooldown_seconds': cooldown}}
            scene['plugins'] = plugins
            if _has_grant(grants, now, principal_type='plugin', principal_id='interest_share',
                          scene_id=scene_id, capability=Capability.INTEREST_SHARE.value) is None:
                grants.append({
                    'grant_id': 'g' + uuid.uuid4().hex, 'revision': 1, 'operator_id': operator_id, 'principal_type': 'plugin',
                    'principal_id': 'interest_share', 'scene_id': scene_id, 'system_scope': None,
                    'capabilities': [Capability.INTEREST_SHARE.value], 'expires_at': None,
                    'resource_policy': None, 'concurrency': None, 'enabled': True,
                })
    data['access']['capability_grants'] = grants
    return data


def preview_broadcast(root, values):
    scene_id = _scene_id(values.get('scene_id'))
    plugin_id = values.get('plugin_id')
    if plugin_id not in BROADCAST_PLUGINS:
        raise ValueError('请选择日历、动态或直播监测来源')
    data = root.model_dump()
    setting = _plugin_state(data, plugin_id)
    if setting.get('config') is None:
        return {'wizard': 'broadcast', 'blocked': f'{plugin_id} 尚未填写全局来源参数',
                'changes': [], 'scene_id': scene_id, 'plugin_id': plugin_id}
    scene = data['scenes'].get(scene_id)
    if scene is None:
        return {'wizard': 'broadcast', 'blocked': '该群尚未出现在根配置的场景表中',
                'changes': [], 'scene_id': scene_id, 'plugin_id': plugin_id}
    if plugin_id == 'asoul_calendar':
        commands = values.get('commands') or ['calendar_today']
        config = {'commands': commands}
    elif plugin_id == 'bilibili_live_sensor':
        names = values.get('live_subscriptions') or []
        if not names:
            return {'wizard': 'broadcast', 'blocked': '请选择已登记的主播名称',
                    'changes': [], 'scene_id': scene_id, 'plugin_id': plugin_id}
        config = {'announcements': ['live_started'] if values.get('announce') else [],
                  'live_subscriptions': names, 'mention_all': bool(values.get('mention_all'))}
    else:
        config = {}
    current = (scene.get('plugins') or {}).get(plugin_id)
    target = {'enabled': True, 'config': config}
    return {'wizard': 'broadcast', 'blocked': None, 'scene_id': scene_id, 'plugin_id': plugin_id,
            'changes': [{'path': f'scenes.{scene_id}.plugins.{plugin_id}', 'from': current, 'to': target,
                         'note': '仅写入本群播报；关闭普通聊天不叫播报配置完成'}]}


def apply_broadcast(data, values):
    preview = preview_broadcast(type('R', (), {'model_dump': lambda self: data})(), values)
    if preview['blocked']:
        raise ValueError(preview['blocked'])
    plugin_id = preview['plugin_id']
    scene_id = preview['scene_id']
    target = preview['changes'][0]['to']
    scene = data['scenes'][scene_id]
    plugins = dict(scene.get('plugins') or {})
    plugins[plugin_id] = target
    scene['plugins'] = plugins
    data['scenes'][scene_id] = scene
    return data


def preview(root, wizard, values, now):
    if wizard == 'python':
        return preview_python(root, values)
    if wizard == 'research':
        return preview_research(root, values, now)
    if wizard == 'broadcast':
        return preview_broadcast(root, values)
    raise ValueError('未知向导')


def apply_values(data, wizard, values, now, *, operator_id):
    if wizard == 'python':
        return apply_python(data, values)
    if wizard == 'research':
        return apply_research(data, values, now, operator_id=operator_id)
    if wizard == 'broadcast':
        return apply_broadcast(data, values)
    raise ValueError('未知向导')
