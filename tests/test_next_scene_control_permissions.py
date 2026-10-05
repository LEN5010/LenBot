"""Actual identity permissions and typed arguments for scene-local controls."""
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from len_bot.next.config import load_host_config
from len_bot.next.platform.onebot_messages import parse_message
from len_bot.next.chat.scene_control import SceneControlArguments, require_control
from len_bot.next.storage.store import Store


def config(root: Path):
    model={'provider':'fixture','model':'synthetic','context_window_tokens':4096}
    source={"compaction": {"input_tokens": 2000}, 'mode':'isolated-multi','bot_id':'onebot:90001','owners':['onebot:70001'],'timezone':'UTC','database':'state.db',
        'permissions':{'admins':['onebot:70002'],'whitelist':['onebot:70004'],'blacklist':['onebot:70005']},
        'onebot':{'mode':'forward_ws','ws_url':'ws://127.0.0.1:9'},
        'models':{'providers':{'fixture':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'unused'}},
                  'roles':{'mind':model}},
        'scenes':{'onebot:group:80001':{'persona':'role','schedules':{'owner':'onebot:70006'}},
                  'onebot:private:70003':{'persona':'role'}}}
    (root/'lenbot.config.json').write_text(json.dumps(source))
    return load_host_config(root)


def test_control_permission_uses_real_scene_role_not_scoped_task_owner(tmp_path):
    cfg=config(tmp_path)
    with Store(cfg.database) as store:
        for uid,role in [('onebot:70003','admin'),('onebot:70005','owner')]:
            raw={'post_type':'message','message_type':'group','self_id':90001,'group_id':80001,
                'user_id':int(uid.split(':', 1)[1]),'message_id':int(uid.split(':', 1)[1]),'time':1790000000,
                'sender':{'nickname':'合成管理员','role':role},'message':[{'type':'text','data':{'text':'合成'}}]}
            store.enqueue(parse_message(raw,own_message_ids=set()),raw,1790000000)
        local=cfg.scene_config('onebot:group:80001')
        for uid in ('onebot:70001','onebot:70002','onebot:70003'):
            require_control(store,local,uid)
        for uid in ('onebot:70004','onebot:70005','onebot:70006','onebot:70007','onebot:90001'):
            with pytest.raises(PermissionError):require_control(store,local,uid)
        with pytest.raises(PermissionError):require_control(store,cfg.scene_config('onebot:private:70003'),'onebot:70003')
        local.chat_control_roles=['whitelist']
        require_control(store,local,'onebot:70004')
        with pytest.raises(PermissionError):require_control(store,local,'onebot:70001')


@pytest.mark.parametrize('args',[
    {'action':'quiet','seconds':60},{'action':'resume'},
    {'action':'quiet','requester':'nickname','seconds':60},
    {'action':'quiet','requester':'onebot:70001','seconds':True},
    {'action':'quiet','requester':'onebot:70001','seconds':0},
    {'action':'quiet','requester':'onebot:70001','seconds':604801},
    {'action':'quiet','requester':'onebot:70001'},
    {'action':'resume','requester':'onebot:70001','seconds':30},
    {'action':'status','direct':'defer'},
    {'action':'status','scene':'onebot:group:80002'},
])
def test_control_arguments_reject_ambiguous_identity_or_scope(args):
    with pytest.raises(ValidationError):SceneControlArguments.model_validate(args)


def test_control_role_configuration_rejects_duplicate_and_unknown_roles(tmp_path):
    config(tmp_path)
    path=tmp_path/'lenbot.config.json';source=json.loads(path.read_text())
    for roles in [['owner','owner'],['unknown']]:
        source['scenes']['onebot:group:80001']['chat_control_roles']=roles
        path.write_text(json.dumps(source))
        with pytest.raises(ValueError,match='chat_control_roles'):load_host_config(tmp_path)


def test_all_tools_role_remains_valid_for_isolated_replay(tmp_path):
    from len_bot.eval.replay import prepare
    config(tmp_path)
    path=tmp_path/'lenbot.config.json';source=json.loads(path.read_text())
    local=source.pop('scenes')['onebot:group:80001']
    source.update(local,mode='isolated',scene='onebot:group:80001',onebot=None,delivery='simulated',
                  evaluation={'profiles':{'direct':{'voice_mode':'direct'}},
                              'sets':{'coherence':str(Path(__file__).parent/'fixtures/eval/replay_cases.json')}})
    path.write_text(json.dumps(source))
    role=tmp_path/'role';role.mkdir()
    (role/'persona.yaml').write_text(json.dumps({'id':'synthetic','name':'合成角色','brief':'合成',
        'behavior':'合成','self_reference':['我'],'aliases':[],'tools':'all','skills':[],'styles':[]}))
    for name,body in [('voice.md','合成'),('boundaries.md','合成'),('examples.yaml','[]')]:
        (role/name).write_text(body)
    cfg,persona,cases,plan=prepare(tmp_path,'coherence','direct')
    assert persona.tools=='all' and cfg.scene=='onebot:group:80001'
    assert cases.cases and plan['profile']=='direct'
