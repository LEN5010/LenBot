from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType
from .config import BrowserPluginConfig


def create(context):
    from .plugin_core import BrowserAgentPlugin
    return BrowserAgentPlugin(context)


PLUGIN = PluginSpec(id="browser_agent", name="受控浏览器", version="0.1.0",
    description="按域名白名单观察网页和像素；默认停用。", config_model=BrowserPluginConfig,
    create=create, plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.browser.timeout_seconds)
