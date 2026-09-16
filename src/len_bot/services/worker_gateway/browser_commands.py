"""Persistent browser command transport over one container's bounded stdio."""
from __future__ import annotations

import asyncio
import base64
import uuid

from len_bot.browser.protocol import BrowserScreenshot, BrowserWorkerReply, MAX_BROWSER_FRAME_BYTES
from len_bot.execution.protocol import ExecutionState, is_terminal


class BrowserCommands:
    async def accept_browser_command(self, execution_id, command):
        record, created = await self.store.record_browser_command(execution_id, command)
        if created:
            key = command.command_id
            self._command_tasks[key] = asyncio.create_task(self._execute_browser_command(execution_id, command))
        return record

    async def _execute_browser_command(self, execution_id, command):
        lock = self._browser_locks.setdefault(execution_id, asyncio.Lock())
        try:
            async with lock:
                request = await self.store.execution_request_of(execution_id)
                execution = await self._stored(execution_id)
                async with asyncio.timeout(max(0, execution.deadline_at - self.clock())):
                    while execution.state in {ExecutionState.ACCEPTED, ExecutionState.STARTING}:
                        await asyncio.sleep(0.1)
                        execution = await self._stored(execution_id)
                    if execution.state is not ExecutionState.RUNNING:
                        raise ValueError('浏览器未在运行，命令未执行')
                    process = self._browser_processes.get(execution_id)
                    if process is None:
                        raise ValueError('浏览器管道不可接续，原页面已失效')
                    current = await self.store.browser_command(execution_id, command.command_id)
                    if current.status != 'accepted':
                        return
                    wire = command.model_dump_json().encode() + b'\n'
                    if len(wire) > MAX_BROWSER_FRAME_BYTES:
                        raise ValueError('浏览器命令超过管道消息上限')
                    # Persist before touching stdin. A lost response is unknown,
                    # including clicks; no restart path sends these bytes again.
                    await self.store.update_browser_command(execution_id, command.command_id, 'running')
                    process.stdin.write(wire)
                    await process.stdin.drain()
                    async with asyncio.timeout(request.browser.timeout_seconds * 2 + 10):
                        reply_bytes = await process.stdout.readline()
                    if not reply_bytes or len(reply_bytes) > MAX_BROWSER_FRAME_BYTES:
                        raise ValueError('浏览器响应丢失或超过消息上限')
                    reply = BrowserWorkerReply.model_validate_json(reply_bytes)
                    if reply.command_id != command.command_id:
                        raise ValueError('浏览器响应身份不匹配')
                    result = reply.snapshot
                    if reply.status == 'completed':
                        if command.operation == 'capture':
                            if not reply.png_base64 or reply.snapshot is not None:
                                raise ValueError('截图响应缺少像素或混入页面结果')
                            png = base64.b64decode(reply.png_base64, validate=True)
                            if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) > 4 * 1024 * 1024:
                                raise ValueError('截图不是受限 PNG')
                            if len(await self.store.artifacts_for(execution_id)) >= self.config.max_artifacts:
                                raise ValueError('浏览器截图数量已达部署产物上限')
                            artifact_id = uuid.uuid4().hex
                            self._write_control_bytes(self.artifacts_store(execution_id) / artifact_id, png)
                            await self.store.register_artifact(execution_id, command.command_id + '.png',
                                len(png), 'image/png', artifact_id=artifact_id)
                            result = BrowserScreenshot(page_ref=command.arguments.page_ref,
                                artifact_id=artifact_id, size_bytes=len(png), area=command.arguments.area)
                        elif reply.snapshot is None or reply.png_base64 is not None:
                            raise ValueError('页面响应缺少快照或混入截图')
                    elif reply.snapshot is not None or reply.png_base64 is not None:
                        raise ValueError('失败命令不能携带成功结果')
                    await self.store.update_browser_command(execution_id, command.command_id,
                        reply.status, result=result, error=reply.error)
        except BaseException as error:
            current = await self.store.browser_command(execution_id, command.command_id)
            if current and current.status in {'accepted', 'running'}:
                uncertain = current.status == 'running'
                await asyncio.shield(self.store.update_browser_command(execution_id, command.command_id,
                    'unknown' if uncertain else 'failed', error=f'{type(error).__name__}: {error}'))
                if uncertain:
                    # A timed-out reply can poison subsequent message pairing.
                    # End the same process instead of sending another command.
                    await asyncio.shield(self.cancel(execution_id, '浏览器命令响应不能确认，关闭原会话'))
            if isinstance(error, asyncio.CancelledError):
                raise
        finally:
            self._command_tasks.pop(command.command_id, None)
