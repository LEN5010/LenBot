from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .config import BilibiliPluginConfig


def create(context):
    from .plugin import BilibiliContentPlugin
    return BilibiliContentPlugin(context)


PLUGIN = PluginSpec(id='bilibili_content', name='哔哩哔哩内容查询工具', version='1.0.0',
    description='读取哔哩哔哩公开视频、搜索结果和用户动态。',
    config_model=BilibiliPluginConfig, create=create, private_tools=True,
    plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.tool_timeout_seconds)
