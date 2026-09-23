"""Compatibility ID for older local configuration.

The implementation is the same scoped work-only plugin as
``builtin/workspace``, including its one-backend configuration model: an old
root configuration that still names ``python_workspace`` keeps loading, but it
reads the same ``worker``/``gateway`` choice as the current plugin rather than
a second, narrower model that could not express a gateway deployment.
"""
from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType

from ..workspace.config import WorkspacePluginConfig


def create(context):
    from ..workspace.plugin import WorkspacePlugin
    return WorkspacePlugin(context)


PLUGIN = PluginSpec(api_version=2, id="python_workspace", name="Python 工作空间", version="0.2.0",
    description="在隔离容器中处理当前任务的工作文件；默认停用。", config_model=WorkspacePluginConfig,
    create=create, plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.call_timeout_seconds)
