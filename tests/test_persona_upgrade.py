import pytest
from len_bot.events.store import EventStore


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
