"""Fixed browser-only container entrypoint; configuration arrives read-only."""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

from len_bot.browser.protocol import BrowserCommand, BrowserSnapshot, BrowserWorkerReply, MAX_BROWSER_FRAME_BYTES
from len_bot.browser.worker_v2 import BrowserWorkerV2
from len_bot.execution.protocol import ExecutionRequest


async def main():
    execution = ExecutionRequest.model_validate_json(Path('/lenbot-control/browser.json').read_bytes())
    if execution.worker_type != 'browser':
        raise ValueError('浏览器入口不能执行脚本')
    egress = json.loads(Path('/lenbot-control/egress.json').read_bytes())
    proxy = urlsplit(egress['proxy_url'])
    worker = BrowserWorkerV2(execution.browser, proxy={
        'server': f'{proxy.scheme}://{proxy.hostname}:{proxy.port}',
        'username': unquote(proxy.username or ''), 'password': unquote(proxy.password or '')})
    try:
        while True:
            wire = await asyncio.to_thread(sys.stdin.buffer.readline, MAX_BROWSER_FRAME_BYTES + 1)
            if not wire:
                break
            if len(wire) > MAX_BROWSER_FRAME_BYTES or not wire.endswith(b'\n'):
                raise ValueError('命令消息超过上限或不完整')
            command = BrowserCommand.model_validate_json(wire)
            if (command.scene_id, command.job_id, command.job_revision) != (
                execution.scene_id, execution.job_id, execution.job_revision):
                raise ValueError('命令归属与只读执行配置不一致')
            try:
                value = await getattr(worker, command.operation)(execution.workspace_id, command.arguments)
                if command.operation != 'capture':
                    value['collected_text'] = worker._get(execution.workspace_id, value['page_ref']).body_text
                reply = BrowserWorkerReply(command_id=command.command_id, status='completed',
                    png_base64=base64.b64encode(value).decode() if command.operation == 'capture' else None,
                    snapshot=None if command.operation == 'capture' else BrowserSnapshot.model_validate(value))
            except Exception as error:
                reply = BrowserWorkerReply(command_id=command.command_id,
                    status='unknown' if command.operation == 'interact' else 'failed',
                    error=f'{type(error).__name__}: {error}'[:2000])
            encoded = reply.model_dump_json().encode() + b'\n'
            if len(encoded) > MAX_BROWSER_FRAME_BYTES:
                raise ValueError('响应消息超过上限')
            sys.stdout.buffer.write(encoded)
            sys.stdout.buffer.flush()
            if reply.status == 'unknown':
                break
    finally:
        await worker.close()


if __name__ == '__main__':
    asyncio.run(main())
