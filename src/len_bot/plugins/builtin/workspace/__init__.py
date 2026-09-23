from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType
from .config import CALL_MARGIN_SECONDS as WORKSPACE_CALL_MARGIN_SECONDS, WorkspacePluginConfig


def create(context):
    from .plugin import WorkspacePlugin
    return WorkspacePlugin(context)


PLUGIN = PluginSpec(api_version=1, id='workspace', name='隔离 Python 工作空间', version='0.2.0',
    description='为已有信息工作提供有归属的离线 Python 与文件产物工具；默认停用。',
    config_model=WorkspacePluginConfig, create=create, plugin_type=PluginType.TOOL,
    permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.call_timeout_seconds)
