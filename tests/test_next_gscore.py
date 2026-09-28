"""GSUID Core protocol boundary using explicit fixtures shaped after the linked protocol sources."""
import json

import pytest

from len_bot.next.builtin_plugins.gscore_adapter.protocol import ImageSize, parse_frame
from len_bot.next.plugin_host import BUILTIN, read_manifest


def frame(**values):
    return json.dumps({'bot_id':'onebot','bot_self_id':'90001','target_type':'group','target_id':'80001',
                       'content':[{'type':'text','data':'合成回复'}], **values})


def test_core_echo_is_optional_not_a_required_frame_identity():
    parsed=parse_frame(frame())
    assert parsed.echo is None and parsed.scene=='group:80001'
    assert parse_frame(frame(echo='actual-wire-token')).echo=='actual-wire-token'
    assert parse_frame(frame(target_type='direct',target_id='70001')).scene=='private:70001'
    parsed=parse_frame(frame(content=[{'type':'image','data':'base64://c3ludGhldGlj'},
                                     {'type':'image_size','data':[8,9]}, {'type':'at','data':'70001'}]))
    assert isinstance(parsed.content[1],ImageSize) and parsed.content[1].data==(8,9)


@pytest.mark.parametrize('changes',[
    {'target_id':80001},{'target_id':'group:80001'},{'bot_self_id':True},
    {'target_type':'channel'},{'bot_id':'another-platform'},
    {'content':[{'type':'text','data':123}]},{'content':[{'type':'at','data':'not-a-qq'}]},
    {'content':[{'type':'node','data':[]}]},{'content':[{'type':'image_size','data':[0,8]}]},
])
def test_core_invalid_frames_fail_with_raw_fragment(changes):
    with pytest.raises(ValueError,match='raw='):
        parse_frame(frame(**changes))


def test_core_manifest_requires_an_explicit_endpoint():
    manifest=read_manifest(BUILTIN/'gscore_adapter')
    model=manifest.values_model()
    with pytest.raises(ValueError):model.model_validate({})
    config=model.model_validate({'ws_url':'ws://127.0.0.1:9/ws/fixture'})
    assert config.access_token=='' and config.max_frame_bytes==20000000
