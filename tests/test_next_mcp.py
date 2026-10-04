"""MCP configuration and typed result boundaries; content samples are synthetic SDK-shaped fixtures."""
import json
from pathlib import Path

import pytest
from mcp import types
from pydantic import ValidationError

from len_bot.next.config import load_host_config
from len_bot.next.configuration.mcp import MCPService
from len_bot.next.tools.mcp_host import MCPToolError, text_result


def config(tmp_path, services):
    value = {"compaction": {"input_tokens": 2000}, 'mode':'isolated-multi','bot_qq':'90001','timezone':'UTC','database':'state.db',
        'onebot':{'mode':'reverse_ws','listen_host':'127.0.0.1','listen_port':0},
        'models':{'providers':{'local':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'synthetic'}},
            'roles':{role:{'provider':'local','model':'fixture','context_window_tokens':8192} for role in ['mind']}},
        'scenes':{'group:80001':{'persona':'role'}},'mcp':services}
    (tmp_path/'lenbot.config.json').write_text(json.dumps(value))
    return load_host_config(tmp_path)


def test_mcp_root_resolves_cwd_and_validates_scene_references(tmp_path):
    service={'enabled':True,'scenes':['group:80001'],'transport':{'type':'stdio','command':'python','args':['server.py'],'env':{'KEY':'synthetic'}}}
    parsed=config(tmp_path,{'fixture':service})
    assert parsed.mcp['fixture'].transport.cwd==tmp_path
    assert parsed.mcp['fixture'].transport.env=={'KEY':'synthetic'}
    assert parsed.scene_config('group:80001').scene=='group:80001'
    service['scenes']=['group:80002']
    with pytest.raises(ValueError,match='unconfigured scenes'):config(tmp_path,{'fixture':service})
    service['scenes']=[]
    with pytest.raises(ValueError,match='at least one scene'):config(tmp_path,{'fixture':service})
    service['enabled']=False
    assert not config(tmp_path,{'fixture':service}).mcp['fixture'].enabled
    with pytest.raises(ValueError,match='MCP names'):config(tmp_path,{'bad__name':service})


@pytest.mark.parametrize('transport',[
    {'type':'http','url':'https://user:secret@example.test/mcp'},
    {'type':'http','url':'https://example.test/mcp?secret=x'},
    {'type':'http','url':'https://example.test/mcp','headers':{'MCP-Session-Id':'invented'}},
    {'type':'http','url':'https://example.test/mcp','headers':{'X-Key':'x','x-key':'y'}},
    {'type':'sse','url':'https://example.test/sse'},
    {'type':'stdio','command':'python','cwd':Path('/tmp'),'env':{'X=Y':'bad'}},
])
def test_mcp_rejects_invalid_transport_boundary(transport):
    with pytest.raises(ValidationError):MCPService(transport=transport)


def test_mcp_result_preserves_text_structure_links_and_original_errors():
    result=types.CallToolResult.model_validate({'content':[{'type':'text','text':'合成正文'},
        {'type':'resource_link','name':'report','uri':'fixture://report'}],
        'structuredContent':{'answer':42},'isError':False})
    output=json.loads(text_result(result,1024))
    assert output['content'][0]['text']=='合成正文'
    assert output['content'][1]['uri']=='fixture://report'
    assert output['structuredContent']=={'answer':42}
    with pytest.raises(MCPToolError,match='合成失败原文'):
        text_result(types.CallToolResult.model_validate({'content':[{'type':'text','text':'合成失败原文'}],'isError':True}),1024)


def test_mcp_binary_result_never_claims_viewing_or_upload():
    result=types.CallToolResult.model_validate({'content':[{'type':'image','mimeType':'image/png','data':'c3ludGhldGlj'}]})
    with pytest.raises(MCPToolError,match='nothing was viewed, played or uploaded'):
        text_result(result,1024)
