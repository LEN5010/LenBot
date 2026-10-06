"""GSUID Core protocol boundary using explicit fixtures shaped after the linked protocol sources."""
import json

import pytest

from len_bot.next.builtin_plugins.gscore_adapter.protocol import ImageSize, parse_frame
from len_bot.next.plugins.host import BUILTIN, read_manifest


def frame(**values):
    return json.dumps({'bot_id':'onebot','bot_self_id':'90001','target_type':'group','target_id':'80001',
                       'content':[{'type':'text','data':'合成回复'}], **values})


def test_core_echo_is_optional_not_a_required_frame_identity():
    parsed=parse_frame(frame())
    assert parsed.echo is None and parsed.scene=='onebot:group:80001'
    assert parse_frame(frame(echo='actual-wire-token')).echo=='actual-wire-token'
    assert parse_frame(frame(target_type='direct',target_id='70001')).scene=='onebot:private:70001'
    parsed=parse_frame(frame(content=[{'type':'image','data':'base64://c3ludGhldGlj'},
                                     {'type':'image_size','data':[8,9]}, {'type':'at','data':'70001'}]))
    assert isinstance(parsed.content[1],ImageSize) and parsed.content[1].data==(8,9)


@pytest.mark.parametrize('changes',[
    {'target_id':80001},{'target_id':'onebot:group:80001'},{'bot_self_id':True},
    {'target_type':'channel'},{'bot_id':'another-platform'},
    {'content':[{'type':'text','data':123}]},{'content':[{'type':'at','data':'not-a-qq'}]},
    {'content':[{'type':'node','data':[]}]},{'content':[{'type':'image_size','data':[0,8]}]},
])
def test_core_invalid_frames_fail_with_raw_fragment(changes):
    with pytest.raises(ValueError,match='raw='):
        parse_frame(frame(**changes))


def test_core_manifest_requires_an_explicit_endpoint():
    manifest=read_manifest(BUILTIN/'gscore_adapter')
    model=manifest.values_model(())
    with pytest.raises(ValueError):model.model_validate({})
    config=model.model_validate({'ws_url':'ws://127.0.0.1:9/ws/fixture'})
    assert config.access_token=='' and config.max_frame_bytes==20000000


@pytest.mark.asyncio
async def test_core_image_links_reject_internal_addresses_before_http(tmp_path):
    import asyncio

    from websockets.asyncio.server import serve

    from len_bot.next.config import load_host_config
    from len_bot.next.plugins.host import PluginHost

    http_requests = []
    async def internal(reader, writer):
        http_requests.append(await reader.readuntil(b'\r\n\r\n'))
        writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\ninternal')
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    ready = asyncio.Event()
    completed = asyncio.get_running_loop().create_future()
    http_server = await asyncio.start_server(internal, '127.0.0.1', 0)
    port = http_server.sockets[0].getsockname()[1]
    links = [f'http://127.0.0.1:{port}/internal', f'http://[::1]:{port}/internal',
             'http://169.254.169.254/latest/meta-data/']

    async def core(websocket):
        try:
            await ready.wait()
            for index, url in enumerate(links):
                await websocket.send(frame(echo=f'image-{index}', content=[{'type': 'image', 'data': 'link://' + url}]))
                reply = json.loads(await websocket.recv())
                assert reply['content'][0]['data'] == {'echo': f'image-{index}', 'id': None}
            completed.set_result(None)
            await websocket.wait_closed()
        except Exception as error:
            if not completed.done():
                completed.set_exception(error)
            raise

    async with http_server, serve(core, '127.0.0.1', 0) as core_server:
        source = {
            'mode': 'isolated-multi', 'bot_id': 'onebot:90001', 'timezone': 'UTC', 'database': 'state.db',
            'delivery': 'simulated', 'onebot': None, 'compaction': {'input_tokens': 2000},
            'models': {'providers': {'sample': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                               'api_key': 'synthetic-unused'}},
                       'roles': {'mind': {'provider': 'sample', 'model': 'mind', 'context_window_tokens': 8192}}},
            'plugins': {'gscore_adapter': {'ws_url': f'ws://127.0.0.1:{core_server.sockets[0].getsockname()[1]}',
                                            'timeout_seconds': 2}},
            'scenes': {'onebot:group:80001': {'persona': 'unused-role', 'plugins': ['gscore_adapter']}},
        }
        (tmp_path / 'lenbot.config.json').write_text(json.dumps(source))
        host = PluginHost(load_host_config(tmp_path), core_tools=set())
        try:
            await host.start()
            assert host.plugins['gscore_adapter'].status == 'running'
            ready.set()
            await asyncio.wait_for(completed, timeout=5)
            errors = host.plugins['gscore_adapter'].errors
            assert len(errors) == 3
            assert all('blocked' in item['error'] for item in errors)
            assert http_requests == []
        finally:
            await host.close()
