from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .plugin import LinkParserPlugin
from .config import LinkParserConfig

def create(context):
    return LinkParserPlugin(context)

PLUGIN = PluginSpec(id='link_parser', name='原生链接解析', version='1.0.0',
    description='解析 B 站视频链接并通过既有工具与发送队列交付结果。', config_model=LinkParserConfig,
    create=create, plugin_type=PluginType.HYBRID, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.request_timeout_seconds)
