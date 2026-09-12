from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType
from .config import WorkspacePluginConfig

# The inner worker timeout covers the container execution only.  The outer tool
# call must also cover input export, result reading and container cleanup, so it
# is strictly larger instead of racing the execution deadline.
WORKSPACE_CALL_MARGIN_SECONDS = 30.0


def create(context):
    from .plugin import WorkspacePlugin
    return WorkspacePlugin(context)


PLUGIN = PluginSpec(id='workspace', name='隔离 Python 工作空间', version='0.2.0',
    description='为已有信息工作提供有归属的离线 Python 与文件产物工具；默认停用。',
    config_model=WorkspacePluginConfig, create=create, plugin_type=PluginType.TOOL,
    permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.worker.timeout_seconds + WORKSPACE_CALL_MARGIN_SECONDS)
