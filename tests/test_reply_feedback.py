import pytest
import httpx
from len_bot.events.store import EventStore
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.config import RuntimeConfig
from len_bot.testing.social import social_result
from len_bot.web.app import create_app


@pytest.mark.asyncio
async def test_feedback_window_excludes_shadow_simulation_and_cross_scene(tmp_path):
    now = [1000.0]
    store = EventStore(str(tmp_path / "feedback.db"), clock=lambda: now[0])
    await store.initialize()
    async def put(kind, scope="group:1", **kwargs):
        e = Event(event_type=kind, scene_id=scope, actor_id="user:1", timestamp=now[0], **kwargs)
        await store.commit_scene_event(event=e, scene_state_data={})
        return e
    try:
        await put(EventType.ACTION_SHADOWED, payload={"message_id":"shadow"})
        await put(EventType.MESSAGE_SENT, metadata={"simulated":True}, payload={"message_id":"sim"})
        sent = await put(EventType.MESSAGE_SENT, payload={"message_id":"real", "content":"结果"})
        assert (await store.reply_feedback("group:1"))[0]["response_status"] == "unknown"
        await put(EventType.GROUP_MESSAGE_RECEIVED, scope="group:2", payload={"reply_to_message_id":"real"})
        now[0] += 1
        quote = await put(EventType.GROUP_MESSAGE_RECEIVED, payload={"reply_to_message_id":"real", "raw_text":"这里的年份错了"})
        for _ in range(16):
            await put(EventType.GROUP_MESSAGE_RECEIVED, payload={"raw_text":"其他聊天"})
        rows = await store.reply_feedback("group:1")
        assert len(rows)==1 and rows[0]["human_count"]==15 and rows[0]["window_closed"]
        assert rows[0]["followups"][0]["quoted"] and rows[0]["labels"]==[]
        assert await store.reply_feedback("group:2")==[]
        await put(EventType.REPLY_FEEDBACK_LABELLED, payload={"sent_event_id":sent.id, "human_event_id":quote.id, "kind":"correction", "comment":"年份不符"})
        assert (await store.reply_feedback("group:1"))[0]["labels"][0]["acceptable"] is None
        with pytest.raises(ValueError):
            await put(EventType.REPLY_FEEDBACK_LABELLED, scope="group:2", payload={"sent_event_id":sent.id, "kind":"naturalness"})
        second = await put(EventType.MESSAGE_SENT, payload={"message_id":"second"})
        now[0] += 300
        await put(EventType.GROUP_MESSAGE_RECEIVED, payload={"reply_to_message_id":"second"})
        row = next(r for r in await store.reply_feedback("group:1") if r["sent_event_id"]==second.id)
        assert row["human_count"]==0 and row["window_closed"] and row["response_status"]=="unknown"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_feedback_panel_labels_do_not_mutate_social_state(tmp_path):
    async def silent(messages): return social_result(reason="观察")
    rt = AgentRuntime(RuntimeConfig(db_path=str(tmp_path / "panel.db")), mock_social_handler=silent)
    await rt.start()
    try:
        sent = Event(event_type=EventType.MESSAGE_SENT, scene_id="group:1", actor_id="user:999", payload={"message_id":"123", "content":"候选已真实发送"})
        await rt.commit_tool_observation(sent)
        actor = await rt.scene_manager.get_or_create_actor(sent.scene_id)
        revision = actor.group_session.social_revision
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(rt)), base_url="http://test") as client:
            assert (await client.get('/api/feedback?scene_id=group:1')).status_code==401
            await client.post('/api/auth/login', json={"username":"admin","password":"lenbot123"})
            assert len((await client.get('/api/feedback?scene_id=group:1')).json())==1
            label={"scene_id":"group:1","sent_event_id":sent.id,"kind":"naturalness","acceptable":False,"comment":"结束仍在追问"}
            saved=await client.post('/api/feedback',json=label)
            assert saved.status_code==200
            from len_bot.memory.writes import validate_memory_proposal
            from len_bot.memory.models import MemoryProposal
            with pytest.raises(ValueError):
                await validate_memory_proposal(rt.event_store._db, MemoryProposal(subject='user:1',kind='preference',
                    key='style',value='不喜欢提问',evidence=[saved.json()['event_id']]),'group:1')
            label['human_event_id']='invented'
            assert (await client.post('/api/feedback',json=label)).status_code==400
        assert actor.group_session.social_revision==revision
        assert len((await rt.event_store.reply_feedback('group:1'))[0]['labels'])==1
        assert await rt.event_store.scene_tasks('group:1')==[]
    finally:
        await rt.stop()


@pytest.mark.asyncio
async def test_quality_examples_upgrade_preserves_v2_manual_edits(tmp_path):
    store = EventStore(str(tmp_path/'persona.db')); await store.initialize()
    try:
        await store.save_dynamic_config('diana-v2', {'applied':True})
        await store.save_dynamic_config('persona_config', {'identity_core':'人工人格'})
        await store._db.execute("INSERT INTO voice_exemplars(id,scene_id,content,context,created_at) VALUES('diana-v2:0','','人工样例','人工语境',1)")
        await store._db.commit()
        preview=await store.preview_diana_persona()
        assert preview['example_count']==15
        assert await store.apply_diana_persona(999,preview['preview_token'])
        examples=await store.list_voice_examples()
        assert len(examples)==16
        assert next(e for e in examples if e['id']=='diana-v2:0')['content']=='人工样例'
        assert (await store.get_dynamic_config('persona_config'))['identity_core']=='人工人格'
        assert not await store.apply_diana_persona(999)
    finally:
        await store.close()
