from len_bot.plugins.api import PluginSpec, PluginPermission, PluginType
from .config import CalendarConfig


def create(context):
    from .plugin import AsoulCalendarPlugin
    return AsoulCalendarPlugin(context)


def validate(config, root):
    if root.plugins['asoul_calendar'].enabled and root.time is None:
        raise ValueError('requires configured time settings')


PLUGIN = PluginSpec(id='asoul_calendar', name='A-SOUL 日程', version='1.0.0',
    description='读取唯一 ICS 来源的真实日程，提供日程工具和精确日程命令。',
    config_model=CalendarConfig, create=create, validate_config=validate,
    plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.tool_timeout_seconds)
