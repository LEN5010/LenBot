from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .config import SearchPluginConfig


def create(context):
    from .plugin import WebSearchToolPlugin
    return WebSearchToolPlugin(context)


PLUGIN = PluginSpec(id='web_search_tool', name='实时联网认知检索', version='1.1.0',
    description='通过 Bing 公开检索查找网页，读取正文、PDF 文本并保留图表入口。',
    config_model=SearchPluginConfig, create=create, private_tools=True,
    plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.tool_timeout_seconds)
