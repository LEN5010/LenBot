"""Resource admission and root validation from actual saved token/message facts."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from len_bot.next.models.limits import (ResourceLimits, ModelBudget, LimitReached, SpeechLimitReached, apply_speech_limits,
                                       check_speech, clear_speech, record_speech, speech_quota)
from len_bot.next.models.slots import ModelSlots
from len_bot.next.platform.messages import Segment
from len_bot.next.storage.store import Store
from len_bot.next.memory.jobs import MemoryJobs


def settings(path, **values):
    return SimpleNamespace(database=path,timezone='UTC',limits=ResourceLimits(**values),
                           scene_timezone=lambda _: 'America/Los_Angeles')


def spend(store, scene, tokens, *, end=True):
    turn=store.start_turn(scene)
    call=store.start_call(turn,'mind',{'provider':'fixture'})
    if end:
        store.end_call(call,{},None,tokens=None if tokens is None else {'input':tokens-10,'output':10,'cached':None})
        store.end_turn(turn,'settled')
    return call


@pytest.mark.parametrize('value',[{'messages_per_hour':0},{'scene_messages_per_hour':{'onebot:group:1':False}},
                                 {'scene_daily_tokens':{'nickname':1}},{'daily_tokens':0},{'daily_tokens':1.5},
                                 {'scene_daily_tokens':{'onebot:group:1':0}},{'daily_model_cost':'1'},{'currency':'USD'}])
def test_invalid_resource_configuration(value):
    with pytest.raises(ValidationError): ResourceLimits.model_validate(value)


def test_global_budget_includes_removed_scenes_and_stopped_trials(tmp_path):
    now=datetime(2026,9,28,18,tzinfo=timezone.utc).timestamp()
    path=tmp_path/'state.db'
    with Store(path,now=lambda:now) as store:
        cfg=settings(path,daily_tokens=300)
        budget=ModelBudget(cfg,store,None);budget.trials_root=tmp_path/'trials'
        spend(store,'onebot:group:89999',100)
        trial=budget.trials_root/'stopped'/'state.db'
        with Store(trial,now=lambda:now) as other: spend(other,'onebot:group:80001',200)
        with pytest.raises(LimitReached,match='全局'):budget.check('onebot:group:80002')
        assert budget.totals(None,now-1,now+1)['tokens']==300


def test_inflight_and_failed_unknown_allow_but_successful_unknown_denies(tmp_path):
    at=[1790618400.]
    with Store(tmp_path/'state.db',now=lambda:at[0]) as store:
        budget=ModelBudget(settings(tmp_path/'state.db',daily_tokens=1000),store,None)
        at[0]+=1
        call=spend(store,'onebot:group:80001',None,end=False)
        budget.check('onebot:group:80001')
        store.end_call(call,None,None,'provider request failed')
        budget.check('onebot:group:80001')
        assert budget.totals(None,at[0]-1,at[0]+1)['settled_unknown_calls']==1
        call=spend(store,'onebot:group:80001',None,end=False)
        store.end_call(call,None,None)
        with pytest.raises(LimitReached,match='没有报告 token'):budget.check('onebot:group:80001')


def test_budget_rechecks_after_waiting_for_slot_and_releases_on_rejection(tmp_path):
    async def run():
        with Store(tmp_path/'state.db',now=lambda:1790618400.) as store:
            slots=ModelSlots(1)
            slots.admit=ModelBudget(settings(tmp_path/'state.db',daily_tokens=100),store,None).check
            async def waiting():
                async with slots.slot(scene='onebot:group:80001'):
                    pytest.fail('settled budget was not applied at actual admission')
            async with slots.slot(scene='onebot:group:80001'):
                queued=asyncio.create_task(waiting());await asyncio.sleep(0)
                spend(store,'onebot:group:80001',100)
            with pytest.raises(LimitReached):await queued
            # Rejecting one allowance does not leak the shared slot.
            slots.admit=None
            async with asyncio.timeout(1):
                async with slots.slot():pass
    asyncio.run(run())


def test_speech_window_slides_per_scene_and_keeps_a_direct_reserve(tmp_path):
    at=[1790618400.]
    with Store(tmp_path/'state.db',now=lambda:at[0]) as store:
        cfg=settings(tmp_path/'state.db',messages_per_hour=2,direct_reserve=1,
                     scene_messages_per_hour={'onebot:group:80002':None});cfg.scene='onebot:group:80001'
        record_speech(store,cfg.scene);at[0]+=600;record_speech(store,cfg.scene)
        with pytest.raises(SpeechLimitReached) as held:check_speech(store,cfg)
        assert held.value.until==1790618400.+3600 and not held.value.direct
        check_speech(store,cfg,direct=True)
        record_speech(store,cfg.scene)
        with pytest.raises(SpeechLimitReached,match='余量'):check_speech(store,cfg,direct=True)
        quota=speech_quota(store,cfg)
        assert (quota.source,quota.used,quota.remaining,quota.reserve_left)==('global',3,0,0)
        # Crossing a clock hour does not reset; the oldest expression leaves after 60 minutes.
        at[0]=1790618400.+3600;check_speech(store,cfg,direct=True)
        with pytest.raises(SpeechLimitReached):check_speech(store,cfg)
        assert speech_quota(store,cfg,'onebot:group:80002').source=='unlimited'
        assert speech_quota(store,cfg,'onebot:private:70001').source=='private'
        for _ in range(5):record_speech(store,'onebot:group:80002')
        cfg.scene='onebot:group:80002';check_speech(store,cfg)
        clear_speech(store,'onebot:group:80001')
        cfg.scene='onebot:group:80001';check_speech(store,cfg)
        assert speech_quota(store,cfg).used==0


def test_speech_settings_apply_without_touching_token_limits():
    running=SimpleNamespace(limits=ResourceLimits(daily_tokens=100))
    saved=SimpleNamespace(limits=ResourceLimits(daily_tokens=200,messages_per_hour=None,direct_reserve=0,
                                                speech_notice_text='先歇一会儿',
                                                scene_messages_per_hour={'onebot:group:80001':3}))
    apply_speech_limits(running,saved)
    assert running.limits==ResourceLimits(daily_tokens=100,messages_per_hour=None,direct_reserve=0,
                                          speech_notice_text='先歇一会儿',
                                          scene_messages_per_hour={'onebot:group:80001':3})
    with pytest.raises(ValidationError):ResourceLimits(speech_notice_text='  ')


@pytest.mark.asyncio
async def test_one_expression_counts_once_and_host_or_plugin_sends_are_free(tmp_path):
    from test_next_persona_permissions import _config,_package
    from len_bot.next.chat.context import ChatContext,turn_state
    from len_bot.next.chat.expression import ChatExpression
    from len_bot.next.chat.tools import SayArguments
    from len_bot.next.persona.profile import load_persona
    from len_bot.next.platform.delivery import split_expression
    from len_bot.plugin import Text
    package=_package(tmp_path,'plain',tools=['say'],documents={})
    config=_config(tmp_path/'instance',package)
    config.limits.messages_per_hour,config.limits.direct_reserve=5,1
    config.text_delivery.max_chars,config.text_delivery.min_interval_seconds,config.text_delivery.max_interval_seconds=4,0,0
    persona=load_persona(package)
    with Store(config.database) as store:
        context=ChatContext(config,persona,store,platform=False,memory=None)
        expression=ChatExpression(config,persona,store,context=context,send_message=None,
                                  notify=lambda:None,on_reply_sample=None,now=store.now)

        async def say(text):
            said=await expression.express('turn',SayArguments(content=text))
            entry=store.prepare_expression(config.scene,'call',text)
            return await expression.send_prepared_expression(entry,split_expression(said.message,4),channels=set())

        await say('一二三四五六七八九十')
        assert store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]==3
        assert speech_quota(store,config).used==1
        notice=store.prepare_limit_notice(config.scene,{},'固定说明')
        await expression.send_prepared_expression(notice,[expression.simulated_message([Segment('text',{'text':'先歇会'})])],
                                                  channels={'limit_notice'},fixed_notice=True)
        await expression.send_plugin_content('clock',[Text('现在 12:00')],reply_to=None)
        assert speech_quota(store,config).used==1
        assert '还剩' not in turn_state(config,store,now=store.now())['content']
        for _ in range(3):await say('嗯')
        assert '还剩 1 条' in turn_state(config,store,now=store.now())['content']
        await say('好')
        assert '还能说 1 条' in turn_state(config,store,now=store.now())['content']
        with pytest.raises(SpeechLimitReached):await say('再说')
        expression.reserve=lambda:True
        await say('被叫到')
        with pytest.raises(SpeechLimitReached):await say('还想说')
        # Fixed notices and plugins still go out when everything is used up.
        await expression.send_plugin_content('clock',[Text('现在 13:00')],reply_to=None)
        assert speech_quota(store,config).used==6


def test_memory_tokens_remain_counted_when_backend_disabled(tmp_path):
    path=tmp_path/'state.db'
    with Store(path,now=lambda:1790618400.) as store:
        with MemoryJobs(path.with_name(path.name+'.memory.sqlite3')) as jobs:
            with jobs.db:
                jobs.db.execute('INSERT INTO memory_summary_runs(scene,scope,path,started,ended,status,request,model_started,tokens) '
                    'VALUES(?,?,?,?,?,?,?,?,?)', ('public','public','index.md',1790618400.,1790618400.,'complete','{}',
                                                 1790618400.,'{"input":90,"output":10,"cached":null}'))
        budget=ModelBudget(settings(path,daily_tokens=100),store,None)
        with pytest.raises(LimitReached):budget.check('onebot:group:80001')


def test_embedding_and_transcription_tokens_do_not_count_toward_the_limit(tmp_path):
    path=tmp_path/'state.db'
    with Store(path,now=lambda:1790618400.) as store:
        with MemoryJobs(path.with_name(path.name+'.memory.sqlite3')) as jobs:
            with jobs.db:
                jobs.db.execute('INSERT INTO memory_embedding_calls(scene,purpose,started,ended,request,tokens) VALUES(?,?,?,?,?,?)',
                    ('public','index',1790618400.,1790618400.,'{}',None))
        ModelBudget(settings(path,daily_tokens=100),store,None).check('onebot:group:80001')


def test_trial_budget_uses_explicit_root_and_requires_migrated_sidecar(tmp_path):
    root=tmp_path/'instance'
    path=root/'nested'/'state.db'
    trial=root/'.runtime'/'chat-tests'/'closed'/'state.db'
    with Store(trial,now=lambda:1790618400.) as other:
        spend(other,'onebot:group:80001',100)
    with Store(path,now=lambda:1790618400.) as store:
        cfg=settings(path,daily_tokens=100)
        with pytest.raises(LimitReached):
            ModelBudget(cfg,store,None,root=root).check('onebot:group:80001')
        with MemoryJobs(trial.with_name(trial.name+'.memory.sqlite3')) as jobs:
            jobs.db.execute('PRAGMA user_version=3')
        with pytest.raises(ValueError,match='migrate_memory_jobs'):
            ModelBudget(cfg,store,None,root=root)


@pytest.mark.parametrize('value',[{'request_days':0},{'timeline_days':31},
    {'message_days':{'nickname':1}},{'message_days':{'onebot:group:1':0}},
    {'message_days':{'onebot:group:1':True}},{'message_days':{'onebot:private:1':'2'}}])
def test_retention_configuration_rejects_ambiguous_or_unmetered_windows(value):
    from len_bot.next.runtime.retention import RetentionSettings
    with pytest.raises(ValidationError):
        RetentionSettings.model_validate(value)


def test_budget_rejects_unmigrated_trial_business_source(tmp_path):
    root=tmp_path/'root'
    path=root/'state.db'
    trial=root/'.runtime'/'chat-tests'/'closed'/'state.db'
    with Store(trial) as old:
        old.db.execute('ALTER TABLE audio_calls DROP COLUMN tokens')
        old.db.execute('PRAGMA user_version=31')
    with Store(path) as store:
        with pytest.raises(ValueError,match='试聊计量源.*离线迁移'):
            ModelBudget(settings(path,daily_tokens=100),store,None,root=root)


@pytest.mark.asyncio
async def test_runner_holds_other_wakes_and_sends_only_the_operator_notice(tmp_path):
    from test_next_persona_permissions import _config,_package
    from len_bot.next.chat.attention import PendingWake,SceneRunner
    from len_bot.next.chat.session import Chat
    from len_bot.next.persona.profile import load_persona
    package=_package(tmp_path,'plain',tools=['say'],documents={})
    config=_config(tmp_path/'instance',package)
    config.limits.messages_per_hour,config.limits.direct_reserve=2,1
    with Store(config.database) as store:
        runner=SceneRunner(Chat(config,load_persona(package),store,None),lambda event:None)
        record_speech(store,config.scene);record_speech(store,config.scene)
        first=store.db.execute('SELECT MIN(time) FROM speech').fetchone()[0]
        assert runner.speech_hold()==first+3600
        runner.state.pending=PendingWake('direct',store.now())
        assert runner.speech_hold() is None
        runner.chat.check_limits(direct=True)
        runner.chat.turn_channel='system';runner.chat.check_limits()
        runner.chat.turn_channel='proactive'
        with pytest.raises(SpeechLimitReached):runner.chat.check_limits()
        record_speech(store,config.scene)
        assert await runner.ready_messages(wait=False) is None
        blocked=SpeechLimitReached('用完',first+3600,direct=True)
        await runner.limit_notice(blocked)
        assert store.db.execute('SELECT COUNT(*) FROM messages').fetchone()[0]==0
        config.limits.speech_notice_text='今天说太多啦，晚点再聊'
        await runner.limit_notice(blocked);await runner.limit_notice(blocked)
        texts=[row[0] for row in store.db.execute("SELECT json_extract(body,'$.segments[0].data.text') FROM messages")]
        assert texts==['今天说太多啦，晚点再聊'] and speech_quota(store,config).used==3


def test_panel_speech_limits_apply_at_once_and_reset_one_scene(tmp_path):
    import json
    import httpx
    from len_bot.next.config import load_host_config
    from len_bot.next.models.client import ChatModel
    from len_bot.next.panel.app import create_app
    from len_bot.next.persona.profile import load_persona
    from len_bot.next.runtime.network import NetworkRuntime
    from len_bot.web.auth import hash_password
    from test_next_persona_permissions import _package
    root=tmp_path/'host';root.mkdir()
    package=_package(root,'persona',tools=[],documents={})
    (root/'lenbot.config.json').write_text(json.dumps({'compaction':{'input_tokens':2000},
        'mode':'isolated-multi','bot_id':'onebot:90001','timezone':'UTC','database':'host.sqlite3',
        'delivery':'simulated','onebot':None,'logging':{'directory':'logs'},
        'panel':{'host':'127.0.0.1','port':0,'username':'operator',
                 'password_hash':hash_password('synthetic-password',salt='synthetic-salt')},
        'models':{'providers':{'local':{'api':'openai-chat','base_url':'http://127.0.0.1:9/v1','api_key':'unused'}},
                  'roles':{'mind':{'provider':'local','model':'synthetic-mind','context_window_tokens':8192}}},
        'scenes':{'onebot:group:80001':{'persona':'persona'},'onebot:private:70001':{'persona':'persona'}}}))
    config=load_host_config(root)
    persona=load_persona(package)

    async def exercise():
        with Store(config.database) as store:
            async with ChatModel(config.model_settings('mind')) as mind:
                runtime=NetworkRuntime(config,[(config.scene_config(scene),persona) for scene in config.scenes],store,mind)
                app=create_app(config,runtime,root=root)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                    assert (await client.post('/api/auth/login',json={'username':'operator','password':'synthetic-password'})).status_code==200
                    group='onebot:group:80001'
                    for _ in range(3):record_speech(store,group)
                    put=lambda body:client.put(f'/api/host/settings/limits/scenes/{group}',json=body)
                    assert (await put({'mode':'custom'})).status_code==422
                    assert (await put({'mode':'global','value':3})).status_code==422
                    assert (await client.put('/api/host/settings/limits/scenes/onebot:private:70001',json={'mode':'unlimited'})).status_code==422
                    snapshot=(await put({'mode':'custom','value':3})).json()
                    assert snapshot['restart_required']['limits'] is False
                    chat=runtime.chats[group]
                    with pytest.raises(SpeechLimitReached):chat.check_limits()
                    state=(await client.get('/api/host/limits')).json()['scenes'][0]
                    assert state['speech']['source']=='scene' and state['speech']['used']==3 and state['speech']['held_until']
                    assert state['blocked'] is False and state['speech']['reserve_left']==5
                    assert (await put({'mode':'unlimited'})).json()['saved']['limits']['scene_messages_per_hour']=={group:None}
                    chat.check_limits()
                    assert (await put({'mode':'global'})).json()['saved']['limits']['scene_messages_per_hour']=={}
                    limits=dict(config.limits.model_dump(mode='json'),daily_tokens=2000,messages_per_hour=2)
                    snapshot=(await client.put('/api/host/settings/limits',json=limits)).json()
                    # Token limits still wait for a restart; the speech limit is already running.
                    assert snapshot['restart_required']['limits'] is True and config.limits.daily_tokens is None
                    with pytest.raises(SpeechLimitReached):chat.check_limits()
                    reset=(await client.post(f'/api/host/scenes/{group}/speech/reset')).json()
                    assert reset['speech']['used']==0
                    chat.check_limits()
                    assert (await client.post('/api/host/scenes/onebot:group:89999/speech/reset')).status_code==404
    asyncio.run(exercise())
