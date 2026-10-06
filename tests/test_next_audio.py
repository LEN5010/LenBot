"""ASR protocol/config boundaries using explicitly synthetic WAV and documented JSON shapes."""
import base64
from io import BytesIO
import wave

import pytest
from pydantic import ValidationError

from len_bot.next.models.asr import ASRBinding, ASRProtocolError, AudioSettings, parse_transcription
from len_bot.next.media.audio import TranscribeArguments
from len_bot.next.platform.onebot_audio import parse_record
from len_bot.next.configuration.models import Models


def wav_bytes():
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x00\x00" * 800)
    return output.getvalue()


def record(data):
    return {"status": "ok", "retcode": 0, "data": {"file": "/napcat/converted.wav", "base64": base64.b64encode(data).decode()}}


def test_audio_conversion_keeps_exact_bytes_and_actual_duration():
    wav = wav_bytes()
    assert parse_record(record(wav), AudioSettings()) == (wav, 0.1)
    with pytest.raises(ValueError, match="not truncated"):
        parse_record(record(wav), AudioSettings(max_seconds=0.05))
    with pytest.raises(ValueError, match="exceeds"):
        parse_record(record(wav), AudioSettings(max_bytes=100))
    with pytest.raises(ValueError, match="truncated"):
        parse_record(record(wav[:-20]), AudioSettings())


@pytest.mark.parametrize("response", [
    {"status": "failed", "retcode": 1400, "wording": "synthetic missing record"},
    {"status": "ok", "retcode": 0, "data": {"file": "/napcat/only-path.wav"}},
    {"status": "ok", "retcode": 0, "data": {"base64": "!invalid!"}},
    record(b"not a WAV"),
])
def test_audio_conversion_failures_include_raw_boundary(response):
    with pytest.raises(ValueError, match="raw="):
        parse_record(response, AudioSettings())


def test_asr_usage_formats_and_empty_transcript_are_not_inferred():
    assert parse_transcription({"text": ""}).text == ""
    assert parse_transcription({"text": ""}).usage is None
    for usage in [{"type": "duration", "seconds": 0.1},
                  {"type": "tokens", "input_tokens": 5, "output_tokens": 2, "total_tokens": 8,
                   "input_token_details": {"audio_tokens": 5}, "provider_extra": 3}]:
        body = {"text": "合成识别文字", "usage": usage, "native": {"value": 1}}
        result = parse_transcription(body)
        assert result.response == body and result.usage == usage


@pytest.mark.parametrize("body", [{}, {"text": None}, {"text": "x", "usage": []},
    {"text": "x", "usage": {"type": "duration", "seconds": float("nan")}},
    {"text": "x", "usage": {"type": "tokens", "input_tokens": True, "output_tokens": 0, "total_tokens": 1}},
    {"text": "x", "usage": {"type": "unknown"}}])
def test_asr_malformed_response_retains_original(body):
    with pytest.raises(ASRProtocolError) as caught:
        parse_transcription(body)
    assert caught.value.response is body
    assert "raw=" in str(caught.value)


def test_asr_configuration_is_explicit_and_does_not_need_chat_parameters():
    selected = ASRBinding(provider="fixture", model="exact-audio", language="zh")
    assert selected.api == "openai-audio"
    for item in [{"api": "openai-chat"}, {"model": " "}, {"language": "auto"}, {"temperature": 0.5}]:
        with pytest.raises(ValidationError):
            ASRBinding.model_validate({**selected.model_dump(), **item})
    model = {"provider": "fixture", "model": "synthetic", "context_window_tokens": 4096}
    source = {"providers": {"fixture": {"api": "openai-chat", "base_url": "http://127.0.0.1:9/v1", "api_key": "synthetic"}},
              "roles": {"mind": model,  "asr": selected.model_dump()}}
    assert Models.model_validate(source).roles.asr == selected
    source["roles"]["asr"]["provider"] = "missing"
    with pytest.raises(ValidationError, match="models.roles.asr.provider"):
        Models.model_validate(source)
    with pytest.raises(ValidationError):
        TranscribeArguments(message=" ", audio=1)


def test_automatic_transcription_requires_explicit_runtime_binding(tmp_path):
    import json
    from len_bot.next.config import load_host_config
    model = {'provider':'fixture','model':'synthetic','context_window_tokens':4096}
    source = {"compaction": {"input_tokens": 2000}, 'mode':'isolated-multi','bot_id':'onebot:90001','timezone':'UTC','database':'state.db',
        'onebot':{'mode':'reverse_ws','listen_host':'127.0.0.1','listen_port':0},
        'models':{'providers':{'fixture':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'synthetic'}},
                  'roles':{'mind':model}},
        'scenes':{'onebot:group:80001':{'persona':'role','transcribe_audio':True}}}
    def load():
        (tmp_path/'lenbot.config.json').write_text(json.dumps(source))
        return load_host_config(tmp_path)
    with pytest.raises(ValueError,match='transcribe_audio requires'):
        load()
    source['models']['roles']['asr']={'provider':'fixture','model':'exact-audio'}
    assert load().scene_config('onebot:group:80001').transcribe_audio is True
    source['delivery']='onebot'
    assert load().scene_config('onebot:group:80001').transcribe_audio is True
    source['scenes']['onebot:group:80001']['transcribe_audio']=False
    source['models']['roles'].pop('asr')
    assert load().models.roles.asr is None
    with pytest.raises(ValidationError):
        AudioSettings(wait_seconds=-1)


@pytest.mark.parametrize('usage,expected',[
    ({'type':'tokens','input_tokens':5,'output_tokens':2,'total_tokens':7,
      'input_token_details':{'audio_tokens':4,'text_tokens':1}}, {'input':5,'output':2,'cached':None}),
    ({'type':'duration','seconds':4}, None),
    (None, None),
])
def test_transcription_tokens_follow_reported_metering(usage,expected):
    from len_bot.next.models.asr import transcription_tokens
    body={'text':'合成识别结果','usage':usage}
    reply=parse_transcription(body)
    assert reply.response is body and reply.usage is usage
    assert transcription_tokens(reply.metering)==expected


def test_asr_binding_has_no_price():
    with pytest.raises(ValidationError):
        ASRBinding(provider='fixture',model='synthetic',price={'type':'duration','currency':'USD','per_second':'1'})


def test_invalid_transcript_retains_independently_valid_usage():
    body={'text':None,'usage':{'type':'tokens','input_tokens':5,'output_tokens':2,'total_tokens':7}}
    with pytest.raises(ASRProtocolError) as caught:
        parse_transcription(body)
    error=caught.value
    assert error.response is body and error.usage is body['usage']
    from len_bot.next.models.asr import transcription_tokens
    assert transcription_tokens(error.metering)=={'input':5,'output':2,'cached':None}
    with pytest.raises(ASRProtocolError) as invalid:
        parse_transcription({'text':None,'usage':{'type':'duration','seconds':-1}})
    assert invalid.value.usage is None and invalid.value.metering is None


def test_daily_token_limit_needs_no_prices(tmp_path):
    import json
    from len_bot.next.config import load_host_config
    binding={'provider':'fixture','model':'synthetic','context_window_tokens':4096}
    source={"compaction": {"input_tokens": 2000}, 'mode':'isolated-multi','bot_id':'onebot:90001','timezone':'UTC','database':'state.db',
        'onebot':{'mode':'reverse_ws','listen_host':'127.0.0.1','listen_port':0},
        'limits':{'daily_tokens':1000000},
        'models':{'providers':{'fixture':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'synthetic'}},
                  'roles':{'mind':binding,'asr':{'provider':'fixture','model':'synthetic-audio'}}},
        'scenes':{'onebot:group:80001':{'persona':'role'}}}
    def load():
        (tmp_path/'lenbot.config.json').write_text(json.dumps(source))
        return load_host_config(tmp_path)
    assert load().limits.daily_tokens==1000000
    source['limits']={'daily_tokens':0}
    with pytest.raises(ValueError):
        load()
    source['limits']={'currency':'USD','daily_model_cost':'1'}
    with pytest.raises(ValueError):
        load()

