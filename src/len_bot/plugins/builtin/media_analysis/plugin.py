import json

from len_bot.media.segment_protocol import SegmentSelection
from len_bot.media.segment_service import SegmentService
from len_bot.media.transcription import TranscribeSegment, transcribe
from len_bot.plugins.api import BasePlugin, ToolResult, ToolSource


class MediaAnalysisPlugin(BasePlugin):
    def __init__(self, context):
        super().__init__(context.manifest)
        self.service = SegmentService(context, context.config)

    async def on_load(self, context):
        context.register_tool('get_video_segment',
            '在当前工作中获取B站指定分P的明确毫秒区间；最多300秒、12帧。下载受实际字节限制，结果只覆盖列出的帧和音轨。',
            SegmentSelection, self.segment, purpose='读取公开视频片段', keywords=('视频', '抽帧', '音轨', '片段'),
            kind='read', roles=('work',), deferred=True)
        context.register_tool('transcribe_video_segment',
            '明确选择转写已取得片段的音轨；可选绑定默认未配置，按当前工作预算计账，ASR可能误识。',
            TranscribeSegment, self.transcribe, purpose='转写指定音频片段', keywords=('音频', '转写', 'ASR'),
            kind='read', roles=('work',), deferred=True)

    async def segment(self, values, call):
        value = await self.service.extract(values, call)
        return ToolResult(status=value['status'], content=json.dumps(value, ensure_ascii=False),
            coverage='sampled_video_frames_and_optional_audio', error_code=value['error_code'],
            sources=[ToolSource(url=value['source_url'], title=f'{values.bvid} cid={values.cid} 指定片段')],
            attachments=[f['asset_id'] for f in value['frames']] + ([value['audio_asset_id']] if value.get('audio_asset_id') else []),
            evidence_kind='external')

    async def transcribe(self, values, call):
        return await transcribe(self.service, values, call)

    async def close_job(self, job):
        await self.service.close_job(job)

    async def on_unload(self):
        await self.service.close()
