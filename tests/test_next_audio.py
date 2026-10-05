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


@pytest.mark.parametrize('price,usage,expected',[
    ({'type':'duration','currency':'USD','per_second':'0.01'}, {'type':'duration','seconds':4}, '0.04'),
    ({'type':'tokens','currency':'USD','input_audio':'10','input_text':'2','output':'3'},
     {'type':'tokens','input_tokens':5,'output_tokens':2,'total_tokens':7,
      'input_token_details':{'audio_tokens':4,'text_tokens':1}}, '0.000048'),
    ({'type':'tokens','currency':'USD','input_audio':'10','input_text':'10','output':'3'},
     {'type':'tokens','input_tokens':5,'output_tokens':2,'total_tokens':7}, '0.000056'),
    ({'type':'tokens','currency':'USD','input_audio':'10','input_text':'2','output':'3'},
     {'type':'tokens','input_tokens':5,'output_tokens':2,'total_tokens':7}, None),
    ({'type':'tokens','currency':'USD','input_audio':'10','input_text':'2','output':'3'},
     {'type':'tokens','input_tokens':5,'output_tokens':2,'total_tokens':7,
      'input_token_details':{'audio_tokens':5,'text_tokens':2}}, None),
    ({'type':'duration','currency':'USD','per_second':'0.01'},
     {'type':'tokens','input_tokens':5,'output_tokens':2,'total_tokens':7}, None),
    ({'type':'duration','currency':'USD','per_second':'0.01'}, None, None),
])
def test_transcription_metering_requires_matching_explicit_rates(price,usage,expected):
    from decimal import Decimal
    from len_bot.next.models.asr import estimate_transcription
    binding=ASRBinding(provider='fixture',model='synthetic',price=price)
    body={'text':'合成识别结果','usage':usage}
    reply=parse_transcription(body)
    amount=estimate_transcription(binding.price,reply.metering)
    assert reply.response is body and reply.usage is usage
    if expected is None:
        assert amount is None
    else:
        assert amount['basis']=='configured_estimate' and amount['currency']=='USD'
        assert Decimal(amount['amount'])==Decimal(expected)


@pytest.mark.parametrize('price',[
    {'type':'duration','currency':'USD','per_second':True},
    {'type':'duration','currency':'USD','per_second':'-1'},
    {'type':'duration','currency':'USD','per_second':'NaN'},
    {'type':'duration','currency':'usd','per_second':'1'},
    {'type':'tokens','currency':'USD','input_audio':'1','output':'1'},
    {'type':'duration','currency':'USD','per_second':'1','input':'1'},
])
def test_asr_price_configuration_never_guesses_a_rate(price):
    with pytest.raises(ValidationError):
        ASRBinding(provider='fixture',model='synthetic',price=price)


def test_invalid_transcript_retains_independently_valid_usage():
    from len_bot.next.models.asr import estimate_transcription
    body={'text':None,'usage':{'type':'duration','seconds':4}}
    binding=ASRBinding(provider='fixture',model='synthetic',price={
        'type':'duration','currency':'USD','per_second':'0.01'})
    with pytest.raises(ASRProtocolError) as caught:
        parse_transcription(body)
    error=caught.value
    assert error.response is body and error.usage is body['usage']
    assert estimate_transcription(binding.price,error.metering)['amount']=='0.040'
    with pytest.raises(ASRProtocolError) as invalid:
        parse_transcription({'text':None,'usage':{'type':'duration','seconds':-1}})
    assert invalid.value.usage is None and invalid.value.metering is None


def test_daily_budget_accepts_asr_only_with_explicit_same_currency_price(tmp_path):
    import json
    from len_bot.next.config import load_host_config
    binding={'provider':'fixture','model':'synthetic','context_window_tokens':4096}
    source={"compaction": {"input_tokens": 2000}, 'mode':'isolated-multi','bot_id':'onebot:90001','timezone':'UTC','database':'state.db',
        'onebot':{'mode':'reverse_ws','listen_host':'127.0.0.1','listen_port':0},
        'limits':{'currency':'USD','daily_model_cost':'1'},
        'models':{'providers':{'fixture':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'synthetic'}},
                  'roles':{'mind':binding,'asr':{'provider':'fixture','model':'synthetic-audio'}},
                  'prices':{'fixture':{'synthetic':{'currency':'USD','input':'1','output':'1','cache_read':'1'}}}},
        'scenes':{'onebot:group:80001':{'persona':'role'}}}
    def load():
        (tmp_path/'lenbot.config.json').write_text(json.dumps(source))
        return load_host_config(tmp_path)
    with pytest.raises(ValueError,match='ASR'):
        load()
    source['models']['roles']['asr']['price']={'type':'duration','currency':'EUR','per_second':'0.01'}
    with pytest.raises(ValueError,match='同币种'):
        load()
    source['models']['roles']['asr']['price']['currency']='USD'
    assert load().models.roles.asr.price.currency=='USD'
