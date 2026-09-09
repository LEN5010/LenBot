from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .config import GroupSummaryConfig
from .work import WORK


def create(context):
    from .plugin import GroupSummaryPlugin
    return GroupSummaryPlugin(context)


def validate(config, root):
    if root.plugins['group_summary'].enabled and root.time is None:
        raise ValueError('requires configured time settings')
    if config.page_chars > root.runtime.tool_result_max_chars:
        raise ValueError('config.page_chars must not exceed runtime.tool_result_max_chars')


PLUGIN = PluginSpec(id='group_summary', name='当前群增量报告', version='2.0.0',
    description='按明确范围分批分析本群已保存记录，复用成功批次，生成结构化报告与图片并沿原工作交付。',
    config_model=GroupSummaryConfig, create=create, validate_config=validate,
    plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.tool_timeout_seconds,work=WORK)
