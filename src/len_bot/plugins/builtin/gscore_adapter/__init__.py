"""Native, opt-in bridge to an independently deployed GSUID Core."""
from .config import GscoreConfig, GscoreSceneConfig
from .protocol import CoreMessageSend

from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType


def create(context):
    from .plugin_core import GscoreAdapterPlugin
    return GscoreAdapterPlugin(context)


def validate(config, root):
    if root.plugins['gscore_adapter'].enabled and not config.ws_url.startswith(('ws://', 'wss://')):
        raise ValueError('gscore_adapter.ws_url must be a ws:// or wss:// URL')


PLUGIN = PluginSpec(
    id='gscore_adapter', name='GSUID Core 游戏桥接', version='0.1.0',
    description='只把明确 /gs 命令桥接到独立 GSUID Core；默认停用。',
    config_model=GscoreConfig, create=create, validate_config=validate,
    scene_config_model=GscoreSceneConfig, plugin_type=PluginType.HYBRID,
    permissions=(PluginPermission.EMIT_EVENT, PluginPermission.REGISTER_TOOL),
    event_models=(('core_message_send', CoreMessageSend),),
    call_timeout=lambda config: config.command_timeout_seconds,
)
