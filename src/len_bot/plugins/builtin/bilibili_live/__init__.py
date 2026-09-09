from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .config import LivePluginConfig, LiveSceneConfig
from .events import LiveSample, LiveEndedSample


def create(context):
    from .plugin import BilibiliLiveSensor
    return BilibiliLiveSensor(context)


def validate(config, root):
    if root.plugins['bilibili_live_sensor'].enabled:
        if root.time is None:
            raise ValueError('requires configured time settings')
        if not root.members:
            raise ValueError('requires configured members')


def validate_scene(config, root):
    unknown = set(config.live_subscriptions) - {member.name for member in root.members}
    if unknown:
        raise ValueError('live_subscriptions references unknown members: ' + ', '.join(sorted(unknown)))


PLUGIN = PluginSpec(id='bilibili_live_sensor', name='哔哩哔哩直播监测', version='0.1.0',
    description='采集共享房间状态，为订阅群的真实新场次生成开播邀请。',
    config_model=LivePluginConfig, create=create, validate_config=validate,
    scene_config_model=LiveSceneConfig, validate_scene_config=validate_scene,
    event_models=(('live_started', LiveSample), ('live_ended', LiveEndedSample)),
    plugin_type=PluginType.HYBRID,
    permissions=(PluginPermission.REGISTER_TOOL, PluginPermission.EMIT_EVENT),
    call_timeout=lambda config: config.tool_timeout_seconds)
