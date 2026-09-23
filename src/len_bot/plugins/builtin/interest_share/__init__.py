from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType
from .config import Candidate, InterestShareConfig, InterestShareScene


def create(context):
    from .plugin import InterestShare
    return InterestShare(context)


def validate(config, root):
    if root.plugins['interest_share'].enabled and root.time is None:
        raise ValueError('公共兴趣分享需要配置 time.timezone')


PLUGIN = PluginSpec(api_version=1, id='interest_share', name='公共兴趣分享', version='0.1.0',
    description='每半小时按本群语境判断一个公共兴趣候选，可保持沉默；需要独立的群级 interest_share 授权。',
    config_model=InterestShareConfig, scene_config_model=InterestShareScene, create=create, validate_config=validate,
    plugin_type=PluginType.SCHEDULED, permissions=(PluginPermission.EMIT_EVENT,),
    event_models=(('candidate', Candidate),))
