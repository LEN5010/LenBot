"""Optional audio binding in the same provider registry and work accounting."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import json
import math

from pydantic import BaseModel, ConfigDict, Field

from len_bot.cognition.budget import WorkBudgetSnapshot, work_call_admission
from len_bot.cognition.projection import estimate_tokens
from len_bot.tools.results import ObservationProvenance, ToolResult, ToolSource


class TranscribeSegment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    execution_id: str = Field(pattern=r'^x[0-9a-f]{32}$')
    result_id: str = Field(min_length=1, description='get_video_segment 实际返回的资料引用')


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)
    text: str


class Transcript(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)
    text: str
    duration: float = Field(gt=0, allow_inf_nan=False)
    segments: list[TranscriptSegment]


async def transcribe(service, values, call):
    runtime, store = service.context._runtime, service.store
    profile = service.config.transcription
    if profile is None:
        return ToolResult(status='unsupported', error_code='capability_missing', content='尚未配置并核对音频转写绑定；可结束为部分结果。')
    job = await service.job(call)
    saved = await service.saved(values.execution_id, call)
    source = await store.read_tool_observation(values.result_id, [call.scene_id])
    if (source is None or source.tool_name != 'get_video_segment' or source.provenance.access != 'anonymous_public'
            or source.status not in {'ok', 'partial'} or json.loads(source.content).get('execution_id') != values.execution_id):
        raise ValueError('转写必须引用本工作实际取得的片段观察')
    if not saved.get('audio_asset_id'):
        raise ValueError('该片段尚无实际回收的音轨；不能从标题推测声音')
    key = f'asr:{job["id"]}:{job["revision"]}:{call.tool_call_id}'
    async with service.mirror.run_lock(key):
        old = await store.query_traces(kind='audio_transcription', ref_id=key, limit=1)
        if old:
            if old[0]['payload']['arguments'] != values.model_dump():
                raise ValueError('同一转写调用参数已改变')
            return ToolResult.model_validate(old[0]['payload']['result'])
        binding = runtime.provider_registry.resolve_transcription(profile)
        _, audio = await service.context.media_service.get_file_bytes(saved['audio_asset_id'], call.scene_id)
        duration = (saved['audio_end_ms'] - saved['audio_start_ms']) / 1000
        snapshot = WorkBudgetSnapshot.model_validate(job['budget'])
        estimate = {'method': 'configured-audio-seconds', 'input_tokens': math.ceil(duration * profile.estimated_tokens_per_second),
            'audio_seconds': duration, 'estimated_tokens_per_second': profile.estimated_tokens_per_second,
            'note': '工作额度估算，不是供应商 token 或费用；实际 duration usage 单列保存'}
        output_tokens = math.ceil(profile.max_text_chars * 1.5)
        base_admission = work_call_admission(store, job['id'], now=runtime.clock)

        async def admission():
            current = await store.get_job(job['id'], call.scene_id)
            if not current or current['revision'] != call.job_revision or current['status'] != 'processing':
                raise ValueError('转写准入时工作已改变')
            duplicate = await (await store._db.execute(
                "SELECT 1 FROM model_calls WHERE episode_id=? AND purpose='audio_transcription' LIMIT 1", (key,))).fetchone()
            if duplicate:
                raise ValueError('此转写已有请求尝试，结果未知时不自动重购')
            if snapshot.job_max_steps is not None and current['model_steps'] + 1 >= snapshot.job_max_steps:
                raise ValueError('转写需要一次模型额度，并为原工作保留最终结论调用')
            held = await base_admission(estimate, output_tokens)
            await store._db.execute('UPDATE agent_jobs SET model_steps=model_steps+1 WHERE id=?', (job['id'],))
            return held

        lease = nullcontext() if call.execution and call.execution.model_slot_owned else runtime._cognition_semaphore
        async with lease:
            await service.job(call)
            call_id = await store.begin_model_call(scene_id=call.scene_id, episode_id=key, job_id=job['id'],
                batch_id=None, role='work', purpose='audio_transcription', provider_id=binding.provider_id,
                model=binding.model, reasoning_effort=None, estimate=estimate,
                output_tokens=output_tokens, admission=admission)
            usage = None
            try:
                remaining = None if snapshot.deadline_at is None else max(0, snapshot.deadline_at - runtime.clock())
                async with asyncio.timeout(remaining):
                    response = await binding.client.audio.transcriptions.create(file=('audio.wav', audio, 'audio/wav'),
                        model=binding.model, response_format='verbose_json', timestamp_granularities=['segment'])
                raw = response.model_dump(mode='json')
                usage = raw.get('usage')
                transcript = Transcript.model_validate(raw)
                if len(transcript.text) > profile.max_text_chars or transcript.duration > duration + 1:
                    raise ValueError('转写正文或时长超过所请求片段范围')
                segments = []
                for segment in transcript.segments:
                    if not segment.start < segment.end <= duration + 0.1:
                        raise ValueError('转写时间戳超出所请求音轨')
                    segments.append({'start_ms': saved['audio_start_ms'] + segment.start * 1000,
                        'end_ms': saved['audio_start_ms'] + segment.end * 1000, 'text': segment.text})
                if sum(len(s['text']) for s in segments) > profile.max_text_chars:
                    raise ValueError('分段转写正文超过限制')
                await service.job(call)
                result = ToolResult(content=json.dumps({'execution_id': values.execution_id,
                    'audio_asset_id': saved['audio_asset_id'], 'text': transcript.text, 'segments': segments,
                    'coverage_start_ms': saved['audio_start_ms'], 'coverage_end_ms': saved['audio_end_ms'],
                    'qualification': 'ASR 派生识别，可能漏词或误识；不能无条件当作精确原话。'}, ensure_ascii=False),
                    coverage='audio_asr_selected_segment', evidence_kind='model',
                    sources=[ToolSource(url=saved['source_url'], title='转写所依据的公开视频片段')],
                    provenance=ObservationProvenance(access='derived', source_result_ids=[values.result_id]))
            except BaseException as error:
                await asyncio.shield(store.end_model_call(call_id,
                    status='cancelled' if isinstance(error, asyncio.CancelledError) else 'failed',
                    usage=usage, error_type=type(error).__name__))
                raise
            await store.end_model_call(call_id, status='completed', usage=usage,
                output_estimate_tokens=estimate_tokens(transcript.text))
            await store.save_trace(kind='audio_transcription', scene_id=call.scene_id, ref_id=key,
                payload={'arguments': values.model_dump(), 'result': result.model_dump(round_trip=True),
                    'job_id': job['id'], 'job_revision': job['revision'], 'model_call_id': call_id})
            return result
