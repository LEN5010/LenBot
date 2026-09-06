"""Native conversation requests through real context, ledger, Actor and Gate."""
import asyncio
import copy
import io
import json
from types import SimpleNamespace

import httpx
from openai import AsyncOpenAI
from PIL import Image
import pytest
import pytest_asyncio

from len_bot.cognition.agent_loop import AgentProtocolError, CommitConflict
from len_bot.cognition.context import ConversationContext
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.providers import RouteResolution
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.events.store import EventStore
from len_bot.media.service import MediaService
from len_bot.memory.store import MemoryStore
from len_bot.runtime.gate import RuntimeGate
from len_bot.scenes.actor import SceneActor, SceneCommitConflict
from len_bot.tools.retrieval import RetrievalToolkit


def call(name,args,ident='call'):
    return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}


def response(*calls,content='内部前言，不能送达',**extra):
    return {'id':'mock','object':'chat.completion','created':0,'model':'native-test',
        'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':content,'tool_calls':list(calls),**extra}}],
        'usage':{'prompt_tokens':20,'completion_tokens':10}}


def text_reply(text):return {'messages':[{'segments':[{'type':'text','text':text}]}]}


@pytest_asyncio.fixture
async def harness(tmp_path):
    store=EventStore(str(tmp_path/'native.db'))
    await store.initialize()
    memory=MemoryStore(store._db,store._write_lock)
    await memory.initialize()
    actor=SceneActor('group:native','user:99',store)
    await actor.start()
    actions=[]
    gate=RuntimeGate(store,SimpleNamespace(enqueue=actions.append),bot_actor_id='user:99')
    async def receive(event):
        if event.scene_id==actor.scene_id:
            actor.post_event(event);await actor._queue.join()
        else:await store.append_event(event)
    runtime=SimpleNamespace(config=RuntimeConfig(db_path=store.db_path),clock=store.clock,bot_actor_id='user:99',
        event_store=store,memory_store=memory,plugin_host=None,receive_event=receive,commit_tool_observation=receive)
    runtime.media_service=MediaService(runtime)
    clients=[]
    async def setup(responses):
        requests=[];remaining=copy.deepcopy(responses)
        async def handle(request):
            requests.append(json.loads(request.content));assert remaining
            value=remaining.pop(0)
            if callable(value):value=await value()
            return httpx.Response(200,json=value)
        client=AsyncOpenAI(api_key='test',base_url='https://fixture.invalid/v1',max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
        clients.append(client)
        runtime.provider_registry=SimpleNamespace(resolve=lambda role:RouteResolution('test','native-test',client,'low',role))
        return requests
    async def human(text='今天终于写完了',ident='human',metadata=None):
        event=Event(id=ident,event_type=EventType.GROUP_MESSAGE_RECEIVED,scene_id=actor.scene_id,actor_id='user:1',
            timestamp=store.clock(),payload={'message_id':ident,'raw_text':text,'sender':{'nickname':'阿明'}},metadata=metadata or {})
        await receive(event);return event
    async def run(ident='turn',observe=None):
        events=await store.get_recent_events(actor.scene_id)
        target=actor.session.last_observed_event_rowid
        mailbox=EpisodeMailbox(ident,actor.scene_id,actor.session.version)
        assert actor.acquire_episode_lease(ident,mailbox)
        audit={};snapshot=actor.session.model_copy(deep=True)
        sources=[e.id for e in events if e.metadata['_rowid']>snapshot.last_cognized_event_rowid]
        async def commit(outcome):return await actor.commit_turn(outcome,target,sources,snapshot.knowledge_revision,mailbox,gate)
        try:
            result=await SocialCognitionCore(runtime).run(snapshot,events,target,ident,sources,observe=observe,commit=commit,trace=audit)
            return result,audit
        finally:actor.release_episode_lease(ident)
    yield SimpleNamespace(runtime=runtime,store=store,memory=memory,actor=actor,actions=actions,setup=setup,human=human,run=run,receive=receive)
    for client in clients:await client.close()
    await runtime.media_service.close();await actor.stop();await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('messages',[[],[{'segments':[{'type':'text','text':'终于解放了，先歇会儿'}]}]])
async def test_one_native_call_only_terminal_messages_can_send(harness,messages):
    h=harness;await h.human();requests=await h.setup([response(call('finish_turn',{'messages':messages}))])
    result,trace=await h.run()
    assert len(requests)==trace['model_calls_used']==1
    assert len(h.actions)==len(messages)
    assert all('内部前言' not in action.content for action in h.actions)
    names={t['function']['name'] for t in requests[0]['tools']}
    assert {'finish_turn','remember','start_work','read_media'}<=names
    assert not names&{'tool_search','calculate','inspect_image','request_deliberate'}
    assert not any('SocialWorld' in json.dumps(message,ensure_ascii=False) for message in requests[0]['messages'])


@pytest.mark.asyncio
async def test_terminal_and_work_are_atomic_and_native_order_independent(harness):
    h=harness;await h.human('帮我查一下这个消息有没有依据')
    await h.setup([response(call('finish_turn',{'messages':[{'segments':[{'type':'text','text':'我查一下来源'}],'ack_ref':'lookup'}]},'end'),
        call('start_work',{'proposal_ref':'lookup','goal':'核对消息来源','evidence':['M1']},'work'))])
    _,trace=await h.run()
    jobs=await h.store.list_jobs(h.actor.scene_id)
    assert len(jobs)==len(h.actions)==1 and jobs[0]['goal']=='核对消息来源'
    assert trace['model_calls_used']==1 and h.actions[0].job_id==jobs[0]['id']


@pytest.mark.asyncio
async def test_invalid_late_memory_rolls_back_staged_job_and_ack(harness):
    h=harness;await h.human()
    await h.setup([response(call('start_work',{'proposal_ref':'work','goal':'检查资料','evidence':['M1']},'w'),
        call('remember',{'subject':'BOT','kind':'preference','statement':'Bot能够进入MC服务器','evidence':['M1']},'b'),
        call('finish_turn',{'messages':[{'segments':[{'type':'text','text':'我记住了'}],'ack_ref':'work'}]},'f'))])
    with pytest.raises(CommitConflict):await h.run()
    assert await h.store.list_jobs(h.actor.scene_id)==[] and not h.actions
    assert await h.memory.query_memories([h.actor.scene_id])==[]
    assert h.actor.session.last_cognized_event_rowid==0


@pytest.mark.asyncio
async def test_native_palette_can_be_selected_without_a_read_tool(harness):
    h=harness;buf=io.BytesIO();Image.new('RGB',(30,30),'orange').save(buf,format='PNG')
    asset=await h.runtime.media_service.upload(buf.getvalue(),'global-safe','开心',['开心'])
    await h.runtime.media_service.edit(asset['id'],'global-safe','开心',['开心'],True,palette_order=1)
    await h.human()
    requests=await h.setup([response(call('finish_turn',{'messages':[{'segments':[{'type':'image','asset_id':'P01'}]}]}))])
    _,trace=await h.run()
    assert len(requests)==1 and h.actions[0].segments[0].asset_id==asset['id']
    assert any(p.get('type')=='image_url' for m in requests[0]['messages'] if isinstance(m['content'],list) for p in m['content'])
    assert 'base64' not in json.dumps(trace)


@pytest.mark.asyncio
async def test_unknown_refs_are_repaired_once_without_sending_partial_output(harness):
    h=harness;await h.human()
    bad=response(call('finish_turn',{'messages':[{'segments':[{'type':'image','asset_id':'other-scene'}]}]}))
    requests=await h.setup([bad,bad])
    with pytest.raises(AgentProtocolError):await h.run()
    assert len(requests)==2 and not h.actions and h.actor.session.last_cognized_event_rowid==0


@pytest.mark.asyncio
async def test_scoped_history_cutoff_and_image_metadata_survive_retrieval(harness):
    h=harness
    first=await h.human('值得记住的原话')
    cutoff=first.metadata['_rowid']
    await h.human('值得记住的新话','new')
    foreign=Event(event_type=EventType.GROUP_MESSAGE_RECEIVED,scene_id='group:foreign',actor_id='user:2',payload={'raw_text':'值得记住的秘密'})
    await h.receive(foreign)
    context=ConversationContext(h.runtime,h.actor.session,cutoff)
    toolkit=RetrievalToolkit(h.store,[h.actor.scene_id,'group:foreign'],h.actor.scene_id,context=context)
    result=await toolkit.execute_result('search_messages',{'query':'值得记住'})
    assert '原话' in result.content and '新话' not in result.content and '秘密' not in result.content
    assert list(context.refs.events.values())==[first.id]
    recent=await h.store.get_recent_events(h.actor.scene_id,through_rowid=cutoff)
    assert [e.id for e in recent]==[first.id]
