from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .config import GroupSummaryConfig


def create(context):
    from .plugin import GroupSummaryPlugin
    return GroupSummaryPlugin(context)


def validate(config, root):
    if root.plugins['group_summary'].enabled and root.time is None:
        raise ValueError('requires configured time settings')
    if config.page_chars > root.runtime.tool_result_max_chars:
        raise ValueError('config.page_chars must not exceed runtime.tool_result_max_chars')


PLUGIN = PluginSpec(id='group_summary', name='当前群按需总结', version='1.0.0',
    description='固定本群范围与快照，复用原工作运行器总结已保存的人类消息。',
    config_model=GroupSummaryConfig, create=create, validate_config=validate,
    plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.tool_timeout_seconds)
