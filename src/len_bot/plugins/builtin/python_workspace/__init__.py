from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType
from .config import WorkspacePluginConfig


def create(context):
    # Compatibility ID for older local configuration; implementation is the
    # same scoped work-only plugin as builtin/workspace.
    from ..workspace.plugin import WorkspacePlugin
    return WorkspacePlugin(context)


PLUGIN = PluginSpec(id="python_workspace", name="Python 工作空间", version="0.1.0",
    description="在隔离容器中处理当前任务的工作文件；默认停用。", config_model=WorkspacePluginConfig,
    create=create, plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.worker.timeout_seconds)
