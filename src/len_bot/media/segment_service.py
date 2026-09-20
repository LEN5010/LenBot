"""Work-owned media extraction through the existing Gateway journal and cancellation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import uuid

from pydantic import TypeAdapter

from len_bot.events.models import Initiator
from len_bot.execution.client import WorkerGatewayClient
from len_bot.execution.protocol import ExecutionRequest, ExecutionState, is_terminal
from len_bot.execution.service import GatewayWorkspaceService
from len_bot.media.bilibili_segment import BilibiliSegmentSource
from len_bot.media.segment_protocol import SegmentManifest
from len_bot.runtime.public_research import verify_public_job


@dataclass(frozen=True)
class MediaScope:
    scene_id: str
    job_id: str
    workspace_id: str


class SegmentService:
    def __init__(self, context, config):
        self.context, self.config, self.store = context, config, context.event_store
        self.workspace_plugin_id, self.gateway_config = self.current_gateway()
        self.client = WorkerGatewayClient(self.gateway_config)
        self.mirror = GatewayWorkspaceService(self.client, self.gateway_config, self.store, 'media_analysis')
        self.source = BilibiliSegmentSource()
        self._live = {}

    def current_gateway(self):
        from len_bot.plugins.builtin.workspace.config import configured_workspace
        workspace = configured_workspace(self.context._runtime.config_store.current)
        if workspace is None or workspace[1].gateway is None:
            raise ValueError('媒体处理需要已配置的 Gateway')
        return workspace[0], workspace[1].gateway

    def check_backend(self):
        if self.current_gateway() != (self.workspace_plugin_id, self.gateway_config):
            raise ValueError('Gateway 配置或所属插件已改变，请重新加载媒体插件；不切换原执行后端')

    async def job(self, call):
        await self.context._host.validate_call(call)
        if not self.context._runtime.config.media_enabled:
            raise ValueError('媒体能力已停用')
        if call.role != 'work' or not call.job_id or not call.tool_call_id:
            raise ValueError('媒体需要当前工作和原生调用身份')
        job = await self.store.get_job(call.job_id, call.scene_id)
        if (not job or job['status'] != 'processing' or job['revision'] != call.job_revision
                or job['requester_qq_uid'] != call.requester_qq_uid):
            raise ValueError('媒体调用不属于当前运行工作修订')
        if not call.requester_qq_uid and not await verify_public_job(self.store, job):
            raise ValueError('系统媒体读取只接受已验证公共研究工作')
        deadline = (job.get('budget') or {}).get('deadline_at')
        if deadline is not None and deadline <= self.store.clock():
            raise ValueError('工作期限已结束')
        self.check_backend()
        return job

    async def close_job(self, job):
        for record in await self.store.executions_for_job(job['scene_id'], job['id']):
            if record.worker_type == 'media' and (not is_terminal(record.state)
                    or record.state is ExecutionState.TERMINATION_UNCONFIRMED):
                await self.cancel(record.execution_id, MediaScope(record.scene_id, record.job_id, record.workspace_id))

    async def cancel(self, ident, scope):
        outcome = await self.mirror._cancel_execution(ident, scope)
        if outcome['status'] == 'unconfirmed':
            raise RuntimeError('媒体容器终止尚未确认，保留原执行占用')
        self._live.pop(ident, None)

    async def close(self):
        errors = []
        try:
            for ident, scope in list(self._live.items()):
                try:
                    await self.cancel(ident, scope)
                except RuntimeError as error:
                    errors.append(str(error))
        finally:
            await self.source.close()
            await self.client.close()
        if errors:
            raise RuntimeError('; '.join(errors))

    async def saved(self, execution_id, call):
        await self.job(call)
        record = await self.store.get_execution(execution_id)
        if (record is None or record.worker_type != 'media' or record.scene_id != call.scene_id
                or record.job_id != call.job_id or record.job_revision != call.job_revision):
            raise ValueError('片段不属于当前工作修订')
        rows = await self.store.query_traces(kind='media_segment', ref_id=execution_id, limit=1)
        if not rows:
            raise ValueError('此执行尚无已回收的媒体片段')
        return rows[0]['payload']

    async def extract(self, selection, call):
        job = await self.job(call)
        scope = MediaScope(job['scene_id'], job['id'], f'media_{job["id"]}_{job["revision"]}')
        async with self.mirror.run_lock(scope.workspace_id):
            job = await self.job(call)
            reviewer = self.context._runtime.action_reviewer
            action = await reviewer.request(job=job, native_call_id=call.tool_call_id,
                action_type='download_media', target=f'{selection.bvid}:{selection.cid}',
                parameters={**selection.model_dump(), 'max_download_bytes': self.config.max_download_bytes})
            await reviewer.approve(action)
            await self.job(call)
            record = None
            for candidate in await self.store.executions_for_job(job['scene_id'], job['id']):
                if candidate.worker_type == 'media' and candidate.job_revision == job['revision']:
                    previous = await self.store.execution_request_of(candidate.execution_id)
                    if previous.review_action_id == action.action_id:
                        record = candidate
                        break
            if record is None:
                conclusion, detail = await self.mirror._reconcile_workspace(scope.workspace_id)
                if conclusion != 'available':
                    raise ValueError(detail)
                media = await self.source.resolve(selection, self.config.max_download_bytes)
                job = await self.job(call)
                deadline = (job.get('budget') or {}).get('deadline_at')
                seconds = min(self.gateway_config.execution_timeout_seconds, self.config.timeout_seconds - 30)
                if deadline is not None:
                    seconds = min(seconds, deadline - self.store.clock())
                if seconds <= 1:
                    raise ValueError('工作剩余期限不足以开始媒体执行')
                request = ExecutionRequest(execution_id='x' + uuid.uuid4().hex,
                    scene_id=job['scene_id'], job_id=job['id'], job_revision=job['revision'],
                    workspace_id=scope.workspace_id, initiator=TypeAdapter(Initiator).validate_python(job['initiator']),
                    worker_type='media', media=media, review_action_id=action.action_id,
                    image_ref=self.config.image_ref, network_policy=self.config.network_policy,
                    egress_authorized=True, deadline_seconds=seconds, deadline_at=deadline)
                record, _ = await self.store.record_execution(request)
                try:
                    self.check_backend()
                except ValueError as error:
                    await self.store.append_execution_event(record.execution_id, 'submission_refused', str(error),
                        state=ExecutionState.FAILED, error=str(error))
                    raise
                self._live[record.execution_id] = scope
                await self.mirror._submit_once(request, scope)
            final = await self.mirror._await_result(record.execution_id, record.deadline_at, scope)
            if final is None:
                await self.cancel(record.execution_id, scope)
                raise ValueError('片段执行结果未知；只核对原 execution_id，不自动重跑')
            if final.state is not ExecutionState.EXITED or final.returncode != 0:
                raise ValueError(f'片段执行已中断：{final.state.value}；没有完成分析')
            self._live.pop(record.execution_id, None)
            await self.job(call)
            saved = await self.store.query_traces(kind='media_segment', ref_id=record.execution_id, limit=1)
            if saved:
                return saved[0]['payload']
            return await self.collect(record.execution_id, selection, call)

    async def artifact(self, listing, path, ceiling):
        matches = [item for item in listing['artifacts'] if item['path'] == path]
        if len(matches) != 1 or not 0 < matches[0]['size_bytes'] <= ceiling:
            raise ValueError('媒体产物缺失、身份重复或超过大小限制：' + path)
        row, content = matches[0], bytearray()
        async for chunk in self.client.artifact_chunks(row['artifact_id']):
            if len(content) + len(chunk) > ceiling:
                raise ValueError('实际产物字节超过读取限制：' + path)
            content.extend(chunk)
        if len(content) != row['size_bytes']:
            raise ValueError('媒体产物未完整回收：' + path)
        return bytes(content)

    async def collect(self, ident, selection, call):
        listing = await self.client.artifacts(ident)
        manifest = SegmentManifest.model_validate_json(await self.artifact(listing, 'segment.json', 65536))
        request = await self.store.execution_request_of(ident)
        if (manifest.execution_id != ident or manifest.bvid != selection.bvid or manifest.cid != selection.cid
                or manifest.requested_start_ms != selection.start_ms or manifest.requested_end_ms != selection.end_ms
                or manifest.source_duration_ms != request.media.source_duration_ms
                or len(manifest.frames) > selection.frames or (manifest.audio_path and not selection.audio)):
            raise ValueError('媒体产物与原请求身份或时间范围不一致')
        if any(not selection.start_ms <= f.source_time_ms < selection.end_ms for f in manifest.frames):
            raise ValueError('采样帧时间点超出明确片段')
        if manifest.audio_path and (manifest.audio_start_ms != selection.start_ms
                or manifest.audio_end_ms is None or not selection.start_ms < manifest.audio_end_ms <= selection.end_ms + 1):
            raise ValueError('音轨时间范围与明确片段不一致')
        result = manifest.model_dump()
        result.update(execution_id=ident, job_id=call.job_id, job_revision=call.job_revision,
            source_url=f'https://www.bilibili.com/video/{selection.bvid}', pixels_loaded=False,
            sampling='按解码后时间戳间隔采样，帧间与区间外未分析',
            network_accounting='downloaded_bytes 为 worker 实收响应体（含索引/关键帧超取）；代理实际收发另见执行事件')
        for index, frame in enumerate(result['frames']):
            await self.job(call)
            body = await self.artifact(listing, frame['path'], 8_000_000)
            asset_id = f'segment_{ident}_{index}'
            await self.context.media_service.save_downloaded(asset_id, call.scene_id, result['source_url'], body,
                'image/png', f'{selection.bvid} cid={selection.cid} {frame["source_time_ms"]}ms 采样帧',
                source_event_id=call.source_event_id, expected_type='image')
            frame['asset_id'] = asset_id
        if manifest.audio_path:
            await self.job(call)
            body = await self.artifact(listing, manifest.audio_path, 10_000_000)
            result['audio_asset_id'] = f'segment_{ident}_audio'
            await self.context.media_service.save_downloaded(result['audio_asset_id'], call.scene_id,
                result['source_url'], body, 'audio/wav', f'{selection.bvid} cid={selection.cid} 指定片段音轨',
                source_event_id=call.source_event_id, expected_type='audio')
        await self.job(call)
        await self.store.save_trace(kind='media_segment', scene_id=call.scene_id, ref_id=ident, payload=result)
        return result
