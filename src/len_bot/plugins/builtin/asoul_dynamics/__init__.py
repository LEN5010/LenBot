from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .config import DynamicsConfig


def create(context):
    from .plugin import AsoulDynamicsPlugin
    return AsoulDynamicsPlugin(context)


def validate(config, root):
    if root.plugins['asoul_dynamics'].enabled:
        if root.time is None:
            raise ValueError('requires configured time settings')
        if not root.members:
            raise ValueError('requires configured members')


PLUGIN = PluginSpec(id='asoul_dynamics', name='A-SOUL 动态查询', version='1.0.0',
    description='读取指定动态站已抓取的内容，提供历史同日与二创查询。',
    config_model=DynamicsConfig, create=create, validate_config=validate,
    plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.tool_timeout_seconds)
