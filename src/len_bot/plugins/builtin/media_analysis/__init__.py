from len_bot.plugins.api import PluginPermission, PluginSpec, PluginType
from .config import MediaAnalysisConfig
from ..workspace.config import configured_workspace


def create(context):
    from .plugin import MediaAnalysisPlugin
    return MediaAnalysisPlugin(context)


def validate(config, root):
    if root.plugins['media_analysis'].enabled:
        workspace = configured_workspace(root)
        if workspace is None or workspace[1].gateway is None:
            raise ValueError('媒体分析需要明确配置的工作空间 Gateway；没有宿主解码入口')
    if config.transcription and config.transcription.provider_id not in {p.id for p in root.models.providers}:
        raise ValueError('转写绑定引用了不存在的供应商')


PLUGIN = PluginSpec(id='media_analysis', name='视频片段与转写', version='0.1.0',
    description='在已有工作中获取明确的公开B站片段，保存采样帧与音频；可选转写默认未配置。',
    config_model=MediaAnalysisConfig, create=create, validate_config=validate,
    plugin_type=PluginType.TOOL, permissions=(PluginPermission.REGISTER_TOOL,),
    call_timeout=lambda config: config.timeout_seconds)
