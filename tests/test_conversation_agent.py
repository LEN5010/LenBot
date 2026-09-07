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


def text_reply(text):return {'messages':[{'segments':[{'text':text}]}]}


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
        committed={source for event in events if event.event_type==EventType.CONVERSATION_COMMITTED for source in event.payload['source_event_ids']}
        sources=[e.id for e in events if e.id not in committed and e.event_type in {EventType.GROUP_MESSAGE_RECEIVED,EventType.PRIVATE_MESSAGE_RECEIVED}]
        async def commit(outcome, *, read_event_ids):return await actor.commit_turn(outcome,target,sorted(read_event_ids),snapshot.knowledge_revision,mailbox,gate)
        try:
            result=await SocialCognitionCore(runtime).run(snapshot,events,target,ident,sources,observe=observe,commit=commit,trace=audit)
            return result,audit
        finally:actor.release_episode_lease(ident)
    yield SimpleNamespace(runtime=runtime,store=store,memory=memory,actor=actor,actions=actions,setup=setup,human=human,run=run,receive=receive)
    for client in clients:await client.close()
    await runtime.media_service.close();await actor.stop();await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('messages',[[],[{'segments':[{'text':'终于解放了，先歇会儿'}]}]])
async def test_one_native_call_only_terminal_messages_can_send(harness,messages):
    h=harness;await h.human();requests=await h.setup([response(call('finish_turn',{'messages':messages}))])
    result,trace=await h.run()
    assert len(requests)==trace['model_calls_used']==1
    assert len(h.actions)==len(messages)
    assert all('内部前言' not in action.content for action in h.actions)
    names={t['function']['name'] for t in requests[0]['tools']}
    assert {'finish_turn','remember','start_work','read_media'}<=names
    assert len(names)==13
    assert not names&{'discard_proposal','cancel_work','resume_work','resolve_wait','refute_memory'}
    assert not any(isinstance(m['content'],str) and m['content'].startswith('运行事实') for m in requests[0]['messages'])
    schema=next(t['function']['parameters'] for t in requests[0]['tools'] if t['function']['name']=='finish_turn')
    assert not any(key in json.dumps(schema) for key in ('$defs','$ref','discriminator'))
    assert not names&{'tool_search','calculate','inspect_image','request_deliberate'}
    assert not any('SocialWorld' in json.dumps(message,ensure_ascii=False) for message in requests[0]['messages'])


@pytest.mark.asyncio
async def test_work_receipt_then_terminal_are_atomic(harness):
    h=harness;await h.human('帮我查一下这个消息有没有依据')
    requests=await h.setup([
        response(call('start_work',{'goal':'核对消息来源','evidence':['M1']},'work')),
        response(call('finish_turn',{'messages':[{'segments':[{'text':'我查一下来源'}],'ack_ref':'S1'}]},'end'))])
    _,trace=await h.run()
    jobs=await h.store.list_jobs(h.actor.scene_id)
    assert len(jobs)==len(h.actions)==1 and jobs[0]['goal']=='核对消息来源'
    assert trace['model_calls_used']==2 and h.actions[0].job_id==jobs[0]['id']
    schema=lambda request: next(t['function']['parameters']['properties']['messages']['items']['properties']
        for t in request['tools'] if t['function']['name']=='finish_turn')
    assert 'ack_ref' not in schema(requests[0])
    assert schema(requests[1])['ack_ref']['enum']==['S1']
    receipt=json.loads(next(m['content'] for m in requests[1]['messages'] if m.get('tool_call_id')=='work'))
    assert receipt['status']=='staged' and receipt['ack_ref']=='S1'


@pytest.mark.asyncio
async def test_invalid_late_memory_rolls_back_staged_job_and_ack(harness):
    h=harness;await h.human()
    await h.setup([response(call('start_work',{'goal':'检查资料','evidence':['M1']},'w')),
        response(
        call('remember',{'subject':'BOT','kind':'preference','statement':'Bot能够进入MC服务器','evidence':['M1']},'b'),
        call('finish_turn',{'messages':[{'segments':[{'text':'我记住了'}],'ack_ref':'S1'}]},'f'))])
    with pytest.raises(CommitConflict):await h.run()
    assert await h.store.list_jobs(h.actor.scene_id)==[] and not h.actions
    assert await h.memory.query_memories([h.actor.scene_id])==[]
    assert not await h.store.event_exists('turn:turn', h.actor.scene_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('segments',[[{'image':'P01'}],[{'text':'先说一句'},{'image':'P01'},{'text':'再接一句'}]])
async def test_native_palette_requires_a_read_tool_before_sending(harness,segments):
    h=harness;buf=io.BytesIO();Image.new('RGB',(30,30),'orange').save(buf,format='PNG')
    asset=await h.runtime.media_service.upload(buf.getvalue(),'global-safe','开心',['开心'])
    await h.runtime.media_service.edit(asset['id'],'global-safe','开心',['开心'],True,palette_order=1)
    original_example=[{'type':'text','text':'参考文字'},{'type':'image','asset_id':asset['id']}]
    example=await h.store.add_voice_example('',context='参考语境',segments=original_example)
    await h.human()
    requests=await h.setup([
        response(call('read_media', {'asset_id':'P01'}, 'read')),
        response(call('finish_turn',{'messages':[{'segments':segments}]}))
    ])
    _,trace=await h.run()
    assert len(requests)==2
    assert [(part.type,part.text if part.type=='text' else part.asset_id) for part in h.actions[0].segments]==[
        ('text',part['text']) if 'text' in part else ('image',asset['id']) for part in segments]
    assert any(p.get('type')=='image_url' for m in requests[1]['messages'] if isinstance(m['content'],list) for p in m['content'])
    sample=next(m['content'] for m in requests[0]['messages'] if isinstance(m['content'],str) and m['content'].startswith('运营编写'))
    assert json.loads(sample.split('finish_turn 参数参考：')[-1])=={
        'messages':[{'segments':[{'text':'参考文字'},{'image':'P01'}]}]}
    stored=next(item for item in await h.store.list_voice_examples() if item['id']==example['id'])
    assert stored['segments']==original_example
    assert 'base64' not in json.dumps(trace)


@pytest.mark.asyncio
async def test_unknown_refs_are_repaired_once_without_sending_partial_output(harness):
    h=harness;await h.human()
    bad=response(call('finish_turn',{'messages':[{'segments':[{'image':'other-scene'}]}]}))
    requests=await h.setup([bad,bad])
    with pytest.raises(AgentProtocolError):await h.run()
    assert len(requests)==2 and not h.actions
    assert not await h.store.event_exists('turn:turn', h.actor.scene_id)


@pytest.mark.asyncio
@pytest.mark.parametrize('part',[{'text':'不能先发这段','image':'P01'},{'image_ref':'P01'}])
async def test_ambiguous_or_legacy_parts_need_explicit_repair_before_sending(harness,part):
    h=harness;await h.human()
    requests=await h.setup([
        response(call('finish_turn',{'messages':[{'segments':[part]}]})),
        response(call('finish_turn',text_reply('确认后的内容'))),
    ])
    _,trace=await h.run()
    assert [action.content for action in h.actions]==['确认后的内容']
    assert len(trace['contract_repairs'])==1
    feedback=next(m['content'] for m in requests[1]['messages'] if m['role']=='tool')
    assert 'messages[0].segments[0]' in feedback and len(feedback)<1200
    assert 'errors.pydantic.dev' not in feedback and 'input_value' not in feedback


@pytest.mark.asyncio
@pytest.mark.parametrize(('text','signal'),[
    ('小然在吗',{'name_matches':['小然']}),
    ('然比的表情包',{'name_matches':['然比']}),
    ('[CQ:at,qq=99] 在吗',{'at_bot':True}),
    ('[CQ:image,file=小然.png]',None),
])
async def test_address_cues_are_read_facts_and_do_not_force_a_reply(harness,text,signal):
    h=harness;event=await h.human(text)
    await h.setup([response(call('finish_turn',{'messages':[]}))])
    _,trace=await h.run()
    assert trace['call_signals']==({event.id:signal} if signal else {})
    assert not h.actions
    committed=next(item for item in await h.store.get_recent_events(h.actor.scene_id) if item.event_type==EventType.CONVERSATION_COMMITTED)
    assert event.id in committed.payload['source_event_ids']


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


@pytest.mark.asyncio
async def test_paged_beliefs_only_grant_references_for_complete_visible_records(harness):
    from len_bot.memory.models import MemoryItem
    from len_bot.tools.results import ToolResult
    h=harness;event=await h.human('别用那个称呼')
    context=ConversationContext(h.runtime,h.actor.session,event.metadata['_rowid'])
    toolkit=RetrievalToolkit(h.store,[h.actor.scene_id],h.actor.scene_id,context=context)
    items=[MemoryItem(id=f'memory:{i}',subject='user:1',scope=h.actor.scene_id,kind='preference',
        statement=f'偏好{i}'+('资料'*200),basis='reported',evidence=[event.id]).model_dump(mode='json') for i in range(2)]
    first=await toolkit._present('query_memory',ToolResult(content=json.dumps(items[:1],ensure_ascii=False)))
    page=await toolkit._present('query_memory',ToolResult(content=json.dumps(items,ensure_ascii=False)),limit=len(first.content))
    assert page.next_offset==1 and 'memory:1' not in context.refs.memories.values()
    assert len(json.loads(page.content))==1
    assert context.refs.editable_memories=={'memory:0'}
    with pytest.raises(ValueError):context.refs.memory_id('B2')
    second=await toolkit._present('query_memory',ToolResult(content=json.dumps(items,ensure_ascii=False)),offset=page.next_offset)
    assert json.loads(second.content)[0]['ref']=='B2'
    assert context.refs.memory_id('B2')=='memory:1'


@pytest.mark.asyncio
async def test_read_media_reloads_evicted_pixels_and_identical_assets_keep_provenance(harness):
    h=harness;assets=[]
    for color in ['red','red','green','blue','yellow','orange','pink']:
        buf=io.BytesIO();Image.new('RGB',(12,12),color).save(buf,format='PNG')
        asset=await h.runtime.media_service.upload(buf.getvalue(),'global-safe',color,[color]);assets.append(asset['id'])
    context=ConversationContext(h.runtime,h.actor.session,0)
    messages=await context.attachments(assets[:6])
    context.limit_image_window(messages)
    assert len(context.attached)==6
    messages.extend(await context.attachments(assets[6:]))
    context.limit_image_window(messages)
    assert assets[0] not in context.attached and assets[1] in context.attached
    assert context.media_manifest[-1]['asset_id']==assets[0]
    reloaded=await context.attachments([assets[0]])
    assert reloaded
    messages.extend(reloaded);context.limit_image_window(messages)
    assert assets[0] in context.attached and assets[1] not in context.attached
    wire=context.model_messages(messages)
    assert sum(part['type']=='image_url' for message in wire for part in message['content'])==6
    assert all('_asset_id' not in part for message in wire for part in message['content'])


@pytest.mark.asyncio
async def test_long_original_ranges_require_full_read_before_work_evidence(harness):
    """Range paging keeps one real source; a partial source cannot authorize work."""
    from pathlib import Path
    from len_bot.cognition.agent_loop import ToolArgumentError
    from len_bot.cognition.proposals import ProposalLedger

    h = harness
    root = Path(__file__).parents[1]
    original = '小然，请梳理这两份工程资料。\n' + '\n'.join(
        (root / path).read_text() for path in ('docs/architecture.md', 'docs/implementation.md'))
    event = await h.human(original, ident='long-original')
    cutoff = h.actor.session.last_observed_event_rowid
    context = ConversationContext(h.runtime, h.actor.session.model_copy(deep=True), cutoff)
    toolkit = RetrievalToolkit(h.store, [h.actor.scene_id], h.actor.scene_id,
        memory_store=h.memory, context=context, on_observation=h.receive)
    assert 'read_message_range' not in {item['function']['name'] for item in toolkit.get_tool_definitions()}
    # A summary/lookup locator can open the same range reader before any raw text.
    reference = context.refs.register_event_locator(event.id)
    assert 'read_message_range' in {item['function']['name'] for item in toolkit.get_tool_definitions()}
    located = await toolkit.execute_result('read_context', {'event_id': reference, 'before': 0, 'after': 0})
    assert json.loads(located.content)['range'] == [0, 0, len(original)]
    assert located.coverage.startswith('original_message_locator') and event.id not in context.refs.read_events
    first = event.model_copy(deep=True)
    first.payload['raw_text'] = original[:400]
    first.metadata['_text_range'] = {'start': 0, 'end': 400, 'total': len(original)}
    context.event_message(first)
    reference = context.refs.register_event_locator(event.id)
    assert event.id not in context.refs.read_events
    assert 'read_message_range' in {item['function']['name'] for item in toolkit.get_tool_definitions()}
    ledger = ProposalLedger(context, 'range-evidence')
    with pytest.raises(ToolArgumentError, match='尚未实际读取'):
        await ledger.stage('start_work', {'goal': '梳理工程资料', 'evidence': [reference]})
    assert not await h.store.list_jobs(h.actor.scene_id)

    denied = await toolkit.execute_result('read_message_range', {'event_id': reference, 'offset': 400})
    assert denied.error_code == 'invalid_arguments'
    oversized = await toolkit.execute_result('read_message_range', {'message_ref': reference, 'offset': 400, 'limit': 8001})
    assert oversized.error_code == 'invalid_arguments'
    await h.receive(Event(id='foreign-range', event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id='group:foreign', actor_id='user:foreign', payload={'raw_text': '别群原话不能被范围读取'}))
    foreign = context.refs.register_event_locator('foreign-range')
    outside = await toolkit.execute_result('read_message_range', {'message_ref': foreign, 'offset': 0})
    assert outside.error_code == 'not_found' and 'foreign-range' not in context.refs.read_events

    offset, text = 400, original[:400]
    while offset < len(original):
        result = await toolkit.execute_result('read_message_range', {'message_ref': reference, 'offset': offset})
        page = json.loads(result.content)
        start, end, total = page['range']
        assert start == offset and total == len(original) and end <= offset + 4000
        assert page['text'] == original[start:end] and page['message_ref'] == reference
        text += page['text']
        offset = end
        assert (event.id in context.refs.read_events) == (offset == len(original))
        if offset < len(original):
            assert page['next_offset'] == result.next_offset == offset
    assert text == original and event.id not in context.refs.partial_events
    assert 'read_message_range' in {item['function']['name'] for item in toolkit.get_tool_definitions()}  # Foreign locator remains unread.
    saved = await h.store.list_tool_observations(h.actor.scene_id)
    assert any(item['tool_name'] == 'read_message_range' for item in saved)
    await ledger.stage('start_work', {'goal': '梳理工程资料', 'evidence': [reference]})
    outcome = await ledger.finish({'messages': []})
    mailbox = EpisodeMailbox('range-evidence', h.actor.scene_id, h.actor.session.version)
    gate = RuntimeGate(h.store, SimpleNamespace(enqueue=h.actions.append), bot_actor_id=h.runtime.bot_actor_id)
    assert h.actor.acquire_episode_lease(mailbox.episode_id, mailbox)
    try:
        decision = await h.actor.commit_turn(outcome, cutoff, sorted(context.refs.read_events),
            h.actor.session.knowledge_revision, mailbox, gate)
        assert decision.accepted and not h.actions
        job = (await h.store.list_jobs(h.actor.scene_id))[0]
        assert job['source_event_ids'] == [event.id]
    finally:
        h.actor.release_episode_lease(mailbox.episode_id)
