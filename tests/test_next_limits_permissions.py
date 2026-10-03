"""Resource admission and root validation from actual saved cost/message facts."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from len_bot.next.models.limits import ResourceLimits, ModelBudget, LimitReached, check_speech
from len_bot.next.models.slots import ModelSlots
from len_bot.next.platform.messages import ChatMessage,Sender,Segment
from len_bot.next.storage.store import Store
from len_bot.next.memory.jobs import MemoryJobs


def settings(path, **values):
    return SimpleNamespace(database=path,timezone='UTC',limits=ResourceLimits(**values),
                           scene_timezone=lambda _: 'America/Los_Angeles')


def cost(store, scene, amount, currency='USD', *, end=True):
    turn=store.start_turn(scene)
    call=store.start_call(turn,'mind',{'provider':'fixture'})
    if end:
        store.end_call(call,{},None,cost=None if amount is None else {'currency':currency,'amount':amount,'basis':'configured_estimate'})
        store.end_turn(turn,'settled')
    return call


@pytest.mark.parametrize('value',[{'messages_per_hour':0},{'scene_messages_per_hour':{'group:1':False}},
                                 {'scene_daily_model_cost':{'nickname':'1'}},{'daily_model_cost':'NaN'},
                                 {'daily_model_cost':'-1'},{'currency':'usd'}])
def test_invalid_resource_configuration(value):
    with pytest.raises(ValidationError): ResourceLimits.model_validate(value)


def test_global_budget_includes_removed_scenes_and_stopped_trials(tmp_path):
    now=datetime(2026,9,28,18,tzinfo=timezone.utc).timestamp()
    path=tmp_path/'state.db'
    with Store(path,now=lambda:now) as store:
        cfg=settings(path,daily_model_cost='0.3')
        budget=ModelBudget(cfg,store,None);budget.trials_root=tmp_path/'trials'
        cost(store,'group:89999','0.1')
        trial=budget.trials_root/'stopped'/'state.db'
        with Store(trial,now=lambda:now) as other: cost(other,'group:80001','0.2')
        with pytest.raises(LimitReached,match='全局'):budget.check('group:80002')
        assert budget.totals(None,now-1,now+1)['known_amounts']=={'USD':Decimal('0.3')}


def test_inflight_is_not_settled_unknown_but_completed_unknown_denies(tmp_path):
    at=[1790618400.]
    with Store(tmp_path/'state.db',now=lambda:at[0]) as store:
        budget=ModelBudget(settings(tmp_path/'state.db',daily_model_cost='1'),store,None)
        at[0]+=1
        call=cost(store,'group:80001',None,end=False)
        budget.check('group:80001')
        store.end_call(call,None,None,'provider did not report usage')
        with pytest.raises(LimitReached,match='费用未知'):budget.check('group:80001')


def test_budget_rechecks_after_waiting_for_slot_and_releases_on_rejection(tmp_path):
    async def run():
        with Store(tmp_path/'state.db',now=lambda:1790618400.) as store:
            slots=ModelSlots(1)
            slots.admit=ModelBudget(settings(tmp_path/'state.db',daily_model_cost='1'),store,None).check
            async def waiting():
                async with slots.slot(scene='group:80001'):
                    pytest.fail('settled budget was not applied at actual admission')
            async with slots.slot(scene='group:80001'):
                queued=asyncio.create_task(waiting());await asyncio.sleep(0)
                cost(store,'group:80001','1')
            with pytest.raises(LimitReached):await queued
            # Rejecting one allowance does not leak the shared slot.
            slots.admit=None
            async with asyncio.timeout(1):
                async with slots.slot():pass
    asyncio.run(run())


def test_hourly_send_gate_is_scene_local_and_counts_unconfirmed(tmp_path):
    at=[1790618400.]
    with Store(tmp_path/'state.db',now=lambda:at[0]) as store:
        cfg=settings(tmp_path/'state.db',messages_per_hour=1);cfg.scene='group:80001'
        msg=ChatMessage('fixture','qq',cfg.scene,None,Sender('90001','fixture',None,None),at[0],
                        [Segment('text',{'text':'合成未确认原话'})],None,False,True,'unconfirmed')
        store.start_outgoing(msg, persona_id='synthetic')
        with pytest.raises(LimitReached):check_speech(store,cfg)
        cfg.scene='group:80002';check_speech(store,cfg)
        cfg.scene='group:80001';at[0]+=3600;check_speech(store,cfg)


def test_memory_costs_remain_counted_when_backend_disabled(tmp_path):
    path=tmp_path/'state.db'
    with Store(path,now=lambda:1790618400.) as store:
        with MemoryJobs(path.with_name(path.name+'.memory.sqlite3')) as jobs:
            with jobs.db:
                jobs.db.execute('INSERT INTO memory_embedding_calls(scene,purpose,started,ended,request,cost) VALUES(?,?,?,?,?,?)',
                    ('public','index',1790618400.,1790618400.,'{}','{"currency":"USD","amount":"1"}'))
        budget=ModelBudget(settings(path,daily_model_cost='1'),store,None)
        with pytest.raises(LimitReached):budget.check('group:80001')


def test_trial_budget_uses_explicit_root_and_requires_migrated_sidecar(tmp_path):
    root=tmp_path/'instance'
    path=root/'nested'/'state.db'
    trial=root/'.runtime'/'chat-tests'/'closed'/'state.db'
    with Store(trial,now=lambda:1790618400.) as other:
        cost(other,'group:80001','1')
    with Store(path,now=lambda:1790618400.) as store:
        cfg=settings(path,daily_model_cost='1')
        with pytest.raises(LimitReached):
            ModelBudget(cfg,store,None,root=root).check('group:80001')
        with MemoryJobs(trial.with_name(trial.name+'.memory.sqlite3')) as jobs:
            jobs.db.execute('PRAGMA user_version=3')
        with pytest.raises(ValueError,match='migrate_memory_jobs'):
            ModelBudget(cfg,store,None,root=root)


@pytest.mark.parametrize('value',[{'request_days':0},{'timeline_days':31},
    {'message_days':{'nickname':1}},{'message_days':{'group:1':0}},
    {'message_days':{'group:1':True}},{'message_days':{'private:1':'2'}}])
def test_retention_configuration_rejects_ambiguous_or_unmetered_windows(value):
    from len_bot.next.runtime.retention import RetentionSettings
    with pytest.raises(ValidationError):
        RetentionSettings.model_validate(value)


def test_budget_rejects_unmigrated_trial_business_source(tmp_path):
    root=tmp_path/'root'
    path=root/'state.db'
    trial=root/'.runtime'/'chat-tests'/'closed'/'state.db'
    with Store(trial) as old:
        old.db.execute('ALTER TABLE audio_calls DROP COLUMN cost')
        old.db.execute('PRAGMA user_version=31')
    with Store(path) as store:
        with pytest.raises(ValueError,match='试聊计量源.*离线迁移'):
            ModelBudget(settings(path,daily_model_cost='1'),store,None,root=root)
