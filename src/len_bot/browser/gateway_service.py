"""Job-revision browser sessions on the existing isolated execution Gateway."""
from __future__ import annotations

import asyncio
import uuid
from pydantic import TypeAdapter

from len_bot.browser.protocol import BrowserCommand, BrowserScreenshot, BrowserSnapshot
from len_bot.events.models import Initiator
from len_bot.execution.client import WorkerGatewayClient, GatewayUnavailable, GatewayResultUnknown, GatewayConflict, GatewayRefused
from len_bot.execution.protocol import ExecutionRequest, ExecutionState, is_terminal
from len_bot.execution.service import GatewayWorkspaceService
from len_bot.execution.admission import require_execution_job
from len_bot.runtime.public_research import verify_public_job


class GatewayBrowserService:
    def __init__(self, context, config, gateway_config):
        self.context, self.config, self.gateway_config = context, config, gateway_config
        self.store = context.event_store
        self.client = WorkerGatewayClient(gateway_config)
        self.mirror = GatewayWorkspaceService(self.client, gateway_config, self.store, 'browser_agent')
        self._locks, self._live_sessions = {}, set()

    async def close(self):
        errors = []
        for ident in list(self._live_sessions):
            try:
                await self._cancel(ident, '浏览器插件停止')
            except RuntimeError as error:
                errors.append(str(error))
        await self.client.close()
        if errors:
            raise RuntimeError('; '.join(errors))

    async def close_job(self, job):
        for record in await self.store.executions_for_job(job['scene_id'], job['id']):
            if record.worker_type == 'browser' and (not is_terminal(record.state)
                    or record.state is ExecutionState.TERMINATION_UNCONFIRMED):
                await self._cancel(record.execution_id, '工作结束，回收浏览器会话')

    async def _cancel(self, ident, reason):
        try:
            outcome = await self.client.cancel(ident, reason)
            await self.mirror._mirror_terminal(outcome.record)
            await self.store.interrupt_browser_commands(ident, reason)
            if outcome.record.state is ExecutionState.TERMINATION_UNCONFIRMED:
                raise RuntimeError('浏览器终止尚未确认，原执行继续占用')
        finally:
            self._live_sessions.discard(ident)

    async def _job(self, call):
        if not call.tool_call_id:
            raise ValueError('浏览器需要已有工作及原生工具调用身份')
        return await require_execution_job(self.store, call, 'browser_agent')

    async def _session(self, job, operation):
        existing = [record for record in await self.store.executions_for_job(job['scene_id'], job['id'])
            if record.worker_type == 'browser' and record.job_revision == job['revision']]
        if existing:
            record = existing[-1]
            if record.execution_id not in self._live_sessions:
                if not is_terminal(record.state) or record.state is ExecutionState.TERMINATION_UNCONFIRMED:
                    await self._cancel(record.execution_id, '宿主已重启，原浏览器页面引用不接续')
                raise ValueError('原浏览器会话已失效；可续读已保存 R，重新浏览需要人工继续工作形成新修订')
            remote = await self.client.get(record.execution_id)
            await self.mirror._mirror_terminal(remote)
            if is_terminal(remote.state) or remote.state is ExecutionState.CANCEL_REQUESTED:
                self._live_sessions.discard(record.execution_id)
                raise ValueError('浏览器会话已结束，原页面引用失效；已保存 R 仍可读取')
            return record
        if operation != 'open':
            raise ValueError('当前工作修订尚未打开浏览器页面')
        seconds = min(1800.0, self.gateway_config.execution_timeout_seconds)
        deadline = (job.get('budget') or {}).get('deadline_at')
        if deadline is not None:
            seconds = min(seconds, deadline - self.store.clock())
        if seconds <= 1:
            raise ValueError('原工作剩余期限不足，不能启动浏览器')
        request = ExecutionRequest(execution_id='x' + uuid.uuid4().hex,
            scene_id=job['scene_id'], job_id=job['id'], job_revision=job['revision'],
            workspace_id=f'browser_{job["id"]}_{job["revision"]}',
            initiator=TypeAdapter(Initiator).validate_python(job['initiator']),
            worker_type='browser', browser=self.config.browser, image_ref=self.config.image_ref,
            network_policy=self.config.network_policy, egress_authorized=True,
            deadline_seconds=seconds, deadline_at=deadline)
        record, _ = await self.store.record_execution(request)
        self._live_sessions.add(record.execution_id)
        try:
            await self.client.submit(request)
        except asyncio.CancelledError:
            await asyncio.shield(self._cancel(record.execution_id, '提交浏览器时工作取消'))
            raise
        except (GatewayUnavailable, GatewayResultUnknown, GatewayConflict):
            # Same id is queried below; neither this request nor a command is replayed.
            pass
        except GatewayRefused as error:
            await self.mirror._mark_refused(record.execution_id, str(error))
            self._live_sessions.discard(record.execution_id)
            raise
        return record

    async def execute(self, operation, values, call):
        job = await self._job(call)
        key = (job['id'], job['revision'])
        async with self._locks.setdefault(key, asyncio.Lock()):
            job = await self._job(call)
            record = await self._session(job, operation)
            command = None
            try:
                # Wait for the one accepted execution to actually run.
                async with asyncio.timeout(max(0, record.deadline_at - self.store.clock())):
                    while True:
                        remote = await self.client.get(record.execution_id)
                        await self.mirror._mirror_terminal(remote)
                        if remote.state is ExecutionState.RUNNING:
                            break
                        if is_terminal(remote.state) or remote.state is ExecutionState.CANCEL_REQUESTED:
                            raise ValueError('浏览器未启动成功或已终止')
                        await asyncio.sleep(self.gateway_config.poll_interval_seconds)
                    await self._job(call)
                    old = await self.store.browser_command(record.execution_id, native_call_id=call.tool_call_id)
                    command = BrowserCommand(command_id=old.request.command_id if old else 'cmd_' + uuid.uuid4().hex,
                        native_call_id=call.tool_call_id, scene_id=job['scene_id'], job_id=job['id'],
                        job_revision=job['revision'], operation=operation, arguments=values)
                    result, created = await self.store.record_browser_command(record.execution_id, command)
                    if created:
                        # Record possible transmission before POST, then query the
                        # same identity if transport fails. Never repeat a click.
                        await self.store.update_browser_command(record.execution_id, command.command_id, 'running')
                        try:
                            result = await self.client.browser_command(record.execution_id, command)
                        except (GatewayUnavailable, GatewayResultUnknown):
                            result = None
                    while result is None or result.status in {'accepted', 'running'}:
                        await asyncio.sleep(self.gateway_config.poll_interval_seconds)
                        result = await self.client.browser_command_status(record.execution_id, command.command_id)
                    if result.execution_id != record.execution_id or result.request != command:
                        raise ValueError('Gateway 命令回执与原调用身份不一致')
                    await self.store.update_browser_command(record.execution_id, command.command_id,
                        result.status, result=result.result, error=result.error)
                    if result.status != 'completed':
                        raise ValueError(result.error or f'浏览器命令 {result.status}')
                    if isinstance(result.result, BrowserScreenshot):
                        content = bytearray()
                        async for chunk in self.client.artifact_chunks(result.result.artifact_id):
                            if len(content) + len(chunk) > 4 * 1024 * 1024:
                                raise ValueError('截图产物超过上限')
                            content.extend(chunk)
                        if len(content) != result.result.size_bytes:
                            raise ValueError('截图字节未完整回收')
                        return bytes(content)
                    if not isinstance(result.result, BrowserSnapshot):
                        raise ValueError('浏览器命令没有可读取结果')
                    return result.result.model_dump(mode='json')
            except BaseException as error:
                if command is not None:
                    await asyncio.shield(self.store.update_browser_command(record.execution_id,
                        command.command_id, 'unknown', error=f'{type(error).__name__}: {error}'))
                try:
                    await asyncio.shield(self._cancel(record.execution_id, '浏览器调用中断，核对原执行终止'))
                except RuntimeError as stop_error:
                    await self.store.save_trace(kind='browser_cleanup', scene_id=job['scene_id'],
                        ref_id=record.execution_id, payload={'error': str(stop_error)})
                raise
