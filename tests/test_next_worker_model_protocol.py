"""Synthetic loopback Chat Completions traffic at the Pi proxy protocol boundary."""

import asyncio
import json

import pytest

from len_bot.next.models.client import ModelSettings
from len_bot.next.work.worker_model import Limits, WorkerModelError, WorkerModelProxy


@pytest.mark.asyncio
async def test_pi_output_budget_is_validated_forwarded_and_recorded_without_retry():
    received, started, finished = [], [], []
    event = {'choices': [{'index': 0, 'delta': {'content': 'synthetic'}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 20, 'completion_tokens': 1}}
    stream = ('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n\n').encode()

    async def serve(reader, writer):
        header = (await reader.readuntil(b'\r\n\r\n')).decode()
        fields = dict(line.split(': ', 1) for line in header.split('\r\n')[1:] if ': ' in line)
        received.append(json.loads(await reader.readexactly(int(fields['Content-Length']))))
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n'
                     + f'Content-Length: {len(stream)}\r\nConnection: close\r\n\r\n'.encode() + stream)
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    def record(request):
        started.append(request)
        return len(started)

    server = await asyncio.start_server(serve, '127.0.0.1', 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        settings = ModelSettings(api='openai-chat', base_url=f'http://127.0.0.1:{port}/v1',
                                 api_key='synthetic-key', model='fixture', max_output_tokens=1024)
        async with WorkerModelProxy(settings, 'fixture', 4096, 'synthetic-task',
                                    Limits(10, 100000, 100000), start_call=record,
                                    finish_call=lambda ident, result: finished.append((ident, result))) as proxy:
            request = {'model': 'fixture', 'stream': True,
                       'messages': [{'role': 'user', 'content': 'synthetic summary material'}]}
            for invalid in (0, -1, 1025, True, 512.0, '512', None):
                with pytest.raises(WorkerModelError, match='max_completion_tokens'):
                    async with proxy.open('synthetic-task', json.dumps({
                        **request, 'max_completion_tokens': invalid}).encode()):
                        pytest.fail('Invalid output budget reached upstream')
            assert not received and not started
            for fields in ({'max_completion_tokens': 256}, {}):
                async with proxy.open('synthetic-task', json.dumps({**request, **fields}).encode()) as response:
                    assert b''.join([chunk async for chunk in response.body]) == stream
    assert [item['max_completion_tokens'] for item in received] == [256, 1024]
    assert [item['request']['max_completion_tokens'] for item in started] == [256, 1024]
    assert started[1]['text_request_estimate_tokens'] - started[0]['text_request_estimate_tokens'] == 768
    assert len(finished) == 2 and all(result['error'] is None for _, result in finished)
