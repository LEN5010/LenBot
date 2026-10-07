"""Native HTTP contracts based on the providers' published response shapes.

Identifiers and signatures are synthetic. Loopback peers verify actual HTTP,
including tool-result continuation and SSE bytes; they do not call paid APIs.
"""
import asyncio
import json

import httpx
import pytest

from len_bot.next.configuration.models import Binding, Provider
from len_bot.next.models.client import ModelSettings, ModelProtocolError
from len_bot.next.models.protocols import build_request, parse_reply
from len_bot.next.models.providers import list_models
from len_bot.next.panel.model_access import probe_model
from len_bot.next.work.worker_model import WorkerModelProxy, Limits


TOOL = {'type': 'function', 'function': {'name': 'connection_check', 'parameters': {'type': 'object', 'properties': {}}}}


def response(api, tool=False):
    if api == 'openai-responses':
        blocks = ([{'type': 'reasoning', 'id': 'rs_fixture', 'summary': [], 'encrypted_content': 'opaque-reasoning'},
                   {'type': 'function_call', 'id': 'fc_fixture', 'call_id': 'call_fixture', 'name': 'connection_check', 'arguments': '{}'}] if tool else
                  [{'type': 'message', 'role': 'assistant', 'id': 'msg_fixture', 'status': 'completed', 'content': [{'type': 'output_text', 'text': '连接成功', 'annotations': []}]}])
        return {'id': 'resp_fixture', 'status': 'completed', 'output': blocks,
                'usage': {'input_tokens': 20, 'output_tokens': 7, 'input_tokens_details': {'cached_tokens': 10}}}
    if api == 'anthropic':
        blocks = ([{'type': 'thinking', 'thinking': 'private analysis', 'signature': 'opaque-signature'},
                   {'type': 'tool_use', 'id': 'call_fixture', 'name': 'connection_check', 'input': {}}] if tool else
                  [{'type': 'text', 'text': '连接成功'}])
        return {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'content': blocks,
                'stop_reason': 'tool_use' if tool else 'end_turn',
                'usage': {'input_tokens': 8, 'output_tokens': 7, 'cache_read_input_tokens': 10, 'cache_creation_input_tokens': 2}}
    return {'candidates': [{'content': {'role': 'model', 'parts': ([{'functionCall': {'name': 'connection_check', 'args': {}, 'id': 'call_fixture'}, 'thoughtSignature': 'opaque-signature'}] if tool else [{'text': '连接成功'}])}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 20, 'candidatesTokenCount': 5, 'thoughtsTokenCount': 2, 'cachedContentTokenCount': 10}}


def sse(api, body):
    if api == 'openai-responses':
        events = [{'type': 'response.completed', 'response': body}]
    elif api == 'anthropic':
        events = [{'type': 'message_start', 'message': {**body, 'content': [], 'stop_reason': None,
                                                     'usage': {**body['usage'], 'output_tokens': 0}}}]
        for index, block in enumerate(body['content']):
            if block['type'] == 'text':
                events.extend([{'type': 'content_block_start', 'index': index, 'content_block': {'type': 'text', 'text': ''}},
                               {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'text_delta', 'text': block['text']}}])
            else:
                events.append({'type': 'content_block_start', 'index': index, 'content_block': block})
            events.append({'type': 'content_block_stop', 'index': index})
        events += [{'type': 'message_delta', 'delta': {'stop_reason': body['stop_reason']}, 'usage': {'output_tokens': 7}}, {'type': 'message_stop'}]
    else:
        # Usage can arrive after a finishReason, so the proxy must consume EOF.
        events = [{k: v for k, v in body.items() if k != 'usageMetadata'}, {'usageMetadata': body['usageMetadata']}]
    return b''.join(((f'event: {event["type"]}\n' if api == 'anthropic' else '') + 'data: ' + json.dumps(event) + '\n\n').encode() for event in events)


async def peer_server(replies, received):
    async def peer(reader, writer):
        header = (await reader.readuntil(b'\r\n\r\n')).decode()
        line, *fields = header.split('\r\n')
        fields = {key.lower(): value.strip() for field in fields if ':' in field for key, value in [field.split(':', 1)]}
        body = json.loads(await reader.readexactly(int(fields.get('content-length', 0)))) if 'content-length' in fields else None
        received.append((line, fields, body))
        status, content_type, content = replies.pop(0)
        writer.write(f'HTTP/1.1 {status} Fixture\r\nContent-Type: {content_type}\r\nContent-Length: {len(content)}\r\nConnection: close\r\n\r\n'.encode())
        for offset in range(0, len(content), 13):
            writer.write(content[offset:offset + 13])
            await writer.drain()
        writer.close()
        await writer.wait_closed()
    return await asyncio.start_server(peer, '127.0.0.1', 0)


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['openai-responses', 'anthropic', 'gemini'])
async def test_native_tool_probe_preserves_ids_signatures_and_real_usage(api):
    first, last = response(api, True), response(api)
    received = []
    server = await peer_server([(200, 'application/json', json.dumps(body).encode()) for body in [first, last]], received)
    async with server:
        endpoint = f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1'
        result = await probe_model(Provider(api=api, base_url=endpoint, api_key='fixture-key'),
                                   Binding(provider='fixture', model='fixture', context_window_tokens=8192), 'tools')
    assert result['calls'] == 2 and result['text'] == '连接成功'
    assert all(u['prompt_tokens'] == 20 and u['completion_tokens'] == 7 and u['prompt_tokens_details']['cached_tokens'] == 10 for u in result['usage'])
    second = received[1][2]
    if api == 'openai-responses':
        assert second['input'][1:3] == first['output']
        assert second['input'][3]['call_id'] == 'call_fixture'
        assert second['store'] is False and second['include'] == ['reasoning.encrypted_content']
    elif api == 'anthropic':
        assert second['messages'][1]['content'] == first['content']
        assert second['messages'][2]['content'][0]['tool_use_id'] == 'call_fixture'
        assert received[0][1]['anthropic-version'] == '2023-06-01'
        assert received[0][1]['x-api-key'] == 'fixture-key'
    else:
        assert second['contents'][1] == first['candidates'][0]['content']
        assert second['contents'][2]['parts'][0]['functionResponse']['id'] == 'call_fixture'
        assert received[0][1]['x-goog-api-key'] == 'fixture-key'
    assert 'private analysis' not in result['text']


@pytest.mark.parametrize('api', ['openai-responses', 'anthropic', 'gemini'])
@pytest.mark.parametrize('failure', ['truncated', 'bad-arguments', 'bad-usage'])
def test_native_bad_reply_never_returns_executable_tools(api, failure):
    body = response(api, True)
    if failure == 'truncated':
        if api == 'openai-responses': body['status'] = 'incomplete'
        elif api == 'anthropic': body['stop_reason'] = 'max_tokens'
        else: body['candidates'][0]['finishReason'] = 'MAX_TOKENS'
    elif failure == 'bad-arguments':
        if api == 'openai-responses': body['output'][1]['arguments'] = '{'
        elif api == 'anthropic': body['content'][1]['input'] = []
        else: body['candidates'][0]['content']['parts'][0]['functionCall']['args'] = []
    else:
        if api == 'gemini': body['usageMetadata']['promptTokenCount'] = -1
        else: body['usage']['input_tokens'] = True
    with pytest.raises(ModelProtocolError) as error:
        parse_reply(ModelSettings(api=api, base_url='http://127.0.0.1/v1', api_key='fixture', model='fixture'), body)
    assert error.value.response == body


@pytest.mark.parametrize('target', ['openai-chat', 'openai-responses', 'anthropic', 'gemini'])
def test_rebinding_projects_completed_foreign_tools_without_reusing_signatures(target):
    old = ModelSettings(api='anthropic', base_url='http://127.0.0.1/v1', api_key='fixture', model='old')
    first = parse_reply(old, response('anthropic', True))
    settings = old.model_copy(update={'api': target, 'model': 'new'})
    messages = [first.message, {'role': 'tool', 'tool_call_id': first.tool_calls[0].id, 'content': '完成的结果'}]
    _, payload = build_request(settings, messages, [TOOL])
    assert '完成的结果' in json.dumps(payload, ensure_ascii=False)
    assert 'opaque-signature' not in json.dumps(payload) and 'private analysis' not in json.dumps(payload)
    with pytest.raises(ValueError, match='尚未完成'):
        build_request(settings, [first.message], [TOOL])


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['openai-responses', 'anthropic', 'gemini'])
@pytest.mark.parametrize('broken', [False, True])
async def test_worker_native_stream_is_byte_transparent_and_incomplete_stream_fails(api, broken):
    stream = sse(api, response(api, True))
    if broken:
        stream = stream[:-8]
    received, started, finished = [], [], []
    server = await peer_server([(200, 'text/event-stream', stream)], received)
    async with server:
        settings = ModelSettings(api=api, base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1', api_key='fixture', model='fixture', temperature=None)
        path, request = build_request(settings, [{'role': 'user', 'content': '执行测试'}], [TOOL])
        if api != 'gemini': request['stream'] = True
        async with WorkerModelProxy(settings, 'fixture', 8192, 'task-token', Limits(10, 100000, 100000),
                                    start_call=lambda value: started.append(value) or len(started),
                                    finish_call=lambda ident, value: finished.append(value)) as proxy:
            if broken:
                with pytest.raises(ModelProtocolError):
                    async with proxy.open('task-token', json.dumps(request).encode()) as output:
                        async for _ in output.body: pass
            else:
                async with proxy.open('task-token', json.dumps(request).encode()) as output:
                    assert b''.join([part async for part in output.body]) == stream
    assert len(received) == len(started) == len(finished) == 1
    if not broken:
        assert finished[0]['tokens'] == {'input': 20, 'output': 7, 'cached': 10}
    else:
        assert finished[0]['error'] is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['openai-chat', 'openai-responses', 'anthropic', 'gemini'])
async def test_model_list_reads_native_pagination_and_does_not_guess_limits(api):
    received = []
    if api == 'gemini':
        replies = [{'models': [{'name': 'models/fixture', 'supportedGenerationMethods': ['generateContent'], 'inputTokenLimit': 8192, 'outputTokenLimit': 1024}], 'nextPageToken': 'next'}, {'models': [{'name': 'models/embedding', 'supportedGenerationMethods': ['embedContent']}]}]
    elif api == 'anthropic':
        replies = [{'data': [{'id': 'fixture'}], 'has_more': True, 'last_id': 'fixture'}, {'data': [{'id': 'second'}], 'has_more': False}]
    else:
        replies = [{'data': [{'id': 'fixture'}]}]
    server = await peer_server([(200, 'application/json', json.dumps(body).encode()) for body in replies], received)
    async with server:
        items = await list_models(Provider(api=api, base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1', api_key='fixture'))
    assert items[0]['id'] == 'fixture'
    if api == 'gemini':
        assert items[0]['context_window_tokens'] == 8192 and len(items) == 1
        assert 'pageToken=next' in received[1][0]
    else:
        assert 'context_window_tokens' not in items[0]
    if api == 'anthropic': assert 'after_id=fixture' in received[1][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('api', ['openai-responses', 'anthropic', 'gemini'])
async def test_native_worker_http_bridge_uses_task_tokens_and_exact_model_path(tmp_path, api):
    import os
    import sys
    from pathlib import Path
    from len_bot.next.work.worker_transport import WorkerTransport
    from len_bot.next.models.providers import auth_headers
    stream = sse(api, response(api))
    received, finished = [], []
    server = await peer_server([(200, 'text/event-stream', stream)], received)
    async with server:
        settings = ModelSettings(api=api, base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1', api_key='host-only-key', model='fixture', temperature=None)
        config = tmp_path / 'bridge.json'
        config.write_text(json.dumps({'port': 0, 'max_request_bytes': 100000}))
        async with WorkerModelProxy(settings, 'fixture', 8192, 'task-key', Limits(5, 100000, 100000), start_call=lambda value: 1, finish_call=lambda ident, value: finished.append(value)) as proxy:
            transport = await WorkerTransport.spawn([sys.executable, str(Path(__file__).resolve().parents[1] / 'src/len_bot/next/work/worker_bridge.py'), str(config)],
                cwd=tmp_path, env=os.environ, stderr_path=tmp_path/'bridge.log', proxy=proxy, startup_timeout_seconds=10)
            try:
                _, payload = build_request(settings, [{'role': 'user', 'content': '测试'}], [])
                if api != 'gemini': payload['stream'] = True
                async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{transport.port}', trust_env=False) as client:
                    result = await client.post(proxy.path + ('?beta=true' if api == 'anthropic' else ''), json=payload, headers=auth_headers(api, 'task-key'))
                    assert result.status_code == 200, result.text
                    assert result.content == stream and b'host-only-key' not in result.content
                transport.raise_if_failed()
            finally:
                await transport.close()
    assert len(received) == len(finished) == 1
    assert finished[0]['error'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(('api', 'tool'), [
    ('openai-responses', {'type': 'web_search_preview'}),
    ('openai-responses', {'type': 'mcp', 'server_url': 'https://example.invalid/mcp'}),
    ('anthropic', {'type': 'web_search_20250305', 'name': 'web_search'}),
    ('gemini', {'googleSearch': {}}),
])
async def test_task_model_refuses_provider_network_tools_before_upstream(api, tool):
    from len_bot.next.work.worker_model import WorkerModelError
    started = []
    settings = ModelSettings(api=api, base_url='http://127.0.0.1:9/v1', api_key='unused', model='fixture', temperature=None)
    async with WorkerModelProxy(settings, 'fixture', 8192, 'task-key', Limits(5, 100000, 100000),
                                start_call=lambda value: started.append(value), finish_call=lambda *_: None) as proxy:
        _, payload = build_request(settings, [{'role': 'user', 'content': '测试'}], [])
        payload['tools'] = [tool]
        if api != 'gemini':
            payload['stream'] = True
        with pytest.raises(WorkerModelError, match='local function'):
            async with proxy.open('task-key', json.dumps(payload).encode()):
                pytest.fail('Provider network tools were forwarded')
    assert not started


@pytest.mark.asyncio
@pytest.mark.parametrize('number', ['NaN', 'Infinity', '1e999'])
async def test_invalid_json_number_is_a_protocol_error_with_a_recordable_raw_fragment(number):
    from len_bot.next.models.client import ChatModel
    from len_bot.next.storage.codec import encode
    received = []
    raw = '{"choices": [], "extra": ' + number + '}'
    server = await peer_server([(200, 'application/json', raw.encode())], received)
    async with server:
        settings = ModelSettings(api='openai-chat', base_url=f'http://127.0.0.1:{server.sockets[0].getsockname()[1]}/v1',
                                 api_key='fixture', model='fixture')
        async with ChatModel(settings) as model:
            with pytest.raises(ModelProtocolError) as failure:
                await model.complete([{'role': 'user', 'content': '测试'}], [])
    assert number in encode(failure.value.response)
    assert len(received) == 1
