"""OneBot WAV/base64 boundary using a synthetic 20-second voice message."""
import asyncio
import base64
from io import BytesIO
import json
import wave

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from len_bot.next.config import HostConfig
from len_bot.next.platform.onebot_audio import parse_record
from len_bot.next.platform.onebot import OneBot


def host_source(onebot):
    binding = {'provider': 'fixture', 'model': 'synthetic', 'context_window_tokens': 4096}
    return {"compaction": {"input_tokens": 2000},
        'mode': 'isolated-multi', 'bot_id': 'onebot:90001', 'timezone': 'UTC', 'database': 'unused.db',
        'delivery': 'onebot', 'onebot': onebot,
        'models': {
            'providers': {'fixture': {'api': 'openai-chat', 'base_url': 'http://127.0.0.1:9/v1',
                                      'api_key': 'synthetic-unused'}},
            'roles': {'mind': binding,
                      'asr': {'provider': 'fixture', 'model': 'synthetic-audio'}},
        },
        'scenes': {'onebot:group:80001': {'persona': 'unused-role'}},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['forward_ws', 'reverse_ws'])
async def test_configured_asr_accepts_wav_above_chat_message_limit(mode):
    output = BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        stream.writeframes(b'\0\0' * (20 * 24000))
    wav = output.getvalue()
    response = {'status': 'ok', 'retcode': 0, 'data': {
        'file': '/fixture/voice.wav', 'base64': base64.b64encode(wav).decode(),
    }}
    assert len(json.dumps(response)) > 1048576

    async def peer(websocket):
        request = json.loads(await websocket.recv())
        assert request['action'] == 'get_record'
        await websocket.send(json.dumps({**response, 'echo': request['echo']}))
        await websocket.wait_closed()

    async def request(bot, config):
        await bot.wait_connected(1)
        result = await bot.call('get_record', {'file': 'synthetic.wav', 'out_format': 'wav'})
        assert parse_record(result, config.audio) == (wav, 20.0)
        assert bot.connected

    if mode == 'forward_ws':
        async with serve(peer, '127.0.0.1', 0) as server:
            port = server.sockets[0].getsockname()[1]
            config = HostConfig.model_validate_json(json.dumps(host_source({
                'mode': mode, 'ws_url': f'ws://127.0.0.1:{port}',
            })))
            async with OneBot(config.onebot, bot_id=config.bot_id,
                              on_event=lambda event: None, on_error=lambda error: None) as bot:
                await request(bot, config)
    else:
        config = HostConfig.model_validate_json(json.dumps(host_source({
            'mode': mode, 'listen_host': '127.0.0.1', 'listen_port': 0,
        })))
        async with OneBot(config.onebot, bot_id=config.bot_id,
                          on_event=lambda event: None, on_error=lambda error: None) as bot:
            port = bot.addresses[0][1]
            async with connect(f'ws://127.0.0.1:{port}', proxy=None, additional_headers={
                'X-Self-ID': config.bot_id.split(':', 1)[1], 'X-Client-Role': 'Universal',
            }) as websocket:
                async with asyncio.TaskGroup() as group:
                    group.create_task(peer(websocket))
                    try:
                        await request(bot, config)
                    finally:
                        await websocket.close()


@pytest.mark.parametrize('audio_bytes', [1024, 16 * 1024 * 1024, 25 * 1024 * 1024])
def test_asr_capacity_default_is_finite_and_tracks_allowed_audio(audio_bytes):
    source = host_source({'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'})
    source['audio'] = {'max_bytes': audio_bytes}
    config = HostConfig.model_validate_json(json.dumps(source))
    required = 4 * ((audio_bytes + 2) // 3) + 65536
    assert config.onebot.max_frame_bytes == max(1048576, required)
    assert 'max_frame_bytes' not in source['onebot']


@pytest.mark.parametrize('onebot', [
    {'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'},
    {'mode': 'reverse_ws', 'listen_host': '127.0.0.1', 'listen_port': 0},
])
def test_asr_explicit_capacity_is_not_silently_changed(onebot):
    from pydantic import ValidationError
    source = host_source({**onebot, 'max_frame_bytes': 1048576})
    with pytest.raises(ValidationError, match='onebot.max_frame_bytes >= 22435160'):
        HostConfig.model_validate_json(json.dumps(source))
    source['onebot']['max_frame_bytes'] = 24 * 1024 * 1024
    config = HostConfig.model_validate_json(json.dumps(source))
    assert config.onebot.max_frame_bytes == 24 * 1024 * 1024
    source['onebot']['max_frame_bytes'] = 1048576
    source['audio'] = {'max_bytes': 512 * 1024}
    config = HostConfig.model_validate_json(json.dumps(source))
    assert config.onebot.max_frame_bytes == 1048576


@pytest.mark.parametrize('mode', ['forward_ws', 'reverse_ws'])
@pytest.mark.parametrize('without_ws_audio', ['no_asr', 'simulated', 'http'])
def test_other_transports_and_disabled_audio_keep_the_message_limit(mode, without_ws_audio):
    onebot = ({'mode': mode, 'ws_url': 'ws://127.0.0.1:9'} if mode == 'forward_ws'
              else {'mode': mode, 'listen_host': '127.0.0.1', 'listen_port': 0})
    source = host_source(onebot)
    if without_ws_audio == 'no_asr':
        del source['models']['roles']['asr']
    elif without_ws_audio == 'simulated':
        source['delivery'] = 'simulated'
    else:
        source['onebot'].update(action_transport='http', http_url='http://127.0.0.1:9')
    assert HostConfig.model_validate_json(json.dumps(source)).onebot.max_frame_bytes == 1048576
    source['onebot']['max_frame_bytes'] = 2048
    assert HostConfig.model_validate_json(json.dumps(source)).onebot.max_frame_bytes == 2048


def test_saved_capacity_remains_explicit_when_audio_limit_changes():
    from pydantic import ValidationError
    source = host_source({'mode': 'forward_ws', 'ws_url': 'ws://127.0.0.1:9'})
    config = HostConfig.model_validate_json(json.dumps(source))
    saved = json.loads(config.model_dump_json())
    restored = HostConfig.model_validate_json(json.dumps(saved))
    assert restored.onebot.max_frame_bytes == config.onebot.max_frame_bytes
    saved['audio']['max_bytes'] = 25 * 1024 * 1024
    with pytest.raises(ValidationError, match='onebot.max_frame_bytes'):
        HostConfig.model_validate_json(json.dumps(saved))
