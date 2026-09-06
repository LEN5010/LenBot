import json
import io
import sqlite3
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image

from len_bot.cognition.diana import PERSONA, PRESET_ID, PREVIOUS_PERSONA, PREVIOUS_EXAMPLES
from len_bot.events.store import EventStore
from len_bot.media.service import MediaService
from len_bot.web.auth import get_current_user
from len_bot.web.routes.voice import router as voice_router


def picture():
    data = io.BytesIO()
    Image.new('RGB', (40, 30), 'red').save(data, format='PNG')
    return data.getvalue()


@pytest_asyncio.fixture
async def persona_store(tmp_path):
    store = EventStore(str(tmp_path / 'persona.db'))
    await store.initialize()
    runtime = SimpleNamespace(config=SimpleNamespace(db_path=store.db_path, media_enabled=True), event_store=store,
        commit_tool_observation=store.append_event, query_service=SimpleNamespace(list_voice_examples=store.list_voice_examples))
    service = MediaService(runtime)
    runtime.media_service = service
    try:
        yield store, service, runtime
    finally:
        await service.close()
        await store.close()


async def palette(service):
    refs = {}
    for index, (name, tag) in enumerate((('celebrate', '开心'), ('wry', '无语')), 1):
        asset = await service.upload(picture(), 'global-safe', tag, [tag])
        await service.edit(asset['id'], 'global-safe', tag, [tag], True, palette_order=index)
        refs[name] = asset['id']
    return refs


@pytest.mark.asyncio
async def test_short_card_upgrade_preserves_manual_edits_and_preview_token(persona_store):
    store, service, _ = persona_store
    refs = await palette(service)
    await store.save_dynamic_config('diana-v3', {'applied': True})
    await store.save_dynamic_config('persona_config', {**PREVIOUS_PERSONA, 'identity_core': '人工人格'})
    manual = await store.add_voice_example('', '人工样例', '人工语境')
    context, content = PREVIOUS_EXAMPLES['diana-v2:1']
    old = await store.add_voice_example('', content, context)
    await store._db.execute("UPDATE voice_exemplars SET id='diana-v2:1' WHERE id=?", (old['id'],))
    await store._db.commit()
    preview = await store.preview_diana_persona()
    assert preview['example_count'] == 6 and preview['missing_media'] == []
    assert preview['disable_example_ids'] == ['diana-v2:1']
    assert next(field for field in preview['fields'] if field['key'] == 'identity_core')['action'] == 'preserve'
    await store.update_voice_example(manual['id'], scene_id='', content='改过的人工样例', context='新语境', tag='手写')
    with pytest.raises(ValueError, match='重新预览'):
        await store.apply_diana_persona(999, preview['preview_token'])
    assert await store.get_dynamic_config(PRESET_ID) is None
    fresh = await store.preview_diana_persona()
    assert await store.apply_diana_persona(999, fresh['preview_token'])
    rows = await store.list_voice_examples()
    assert len(rows) == 8
    assert next(item for item in rows if item['id'] == manual['id'])['content'] == '改过的人工样例'
    assert not next(item for item in rows if item['id'] == 'diana-v2:1')['enabled']
    image_only = next(item for item in rows if item['id'] == 'diana-v4:4')
    mixed = next(item for item in rows if item['id'] == 'diana-v4:5')
    assert image_only['segments'] == [{'type': 'image', 'asset_id': refs['celebrate']}]
    assert [part['type'] for part in mixed['segments']] == ['text', 'image']
    assert mixed['segments'][1]['asset_id'] == refs['wry']
    current = await store.get_dynamic_config('persona_config')
    assert current['identity_core'] == '人工人格' and current['conversation_style'] == PERSONA['conversation_style']
    assert not await store.apply_diana_persona(999)
    await store.close()
    await store.initialize()
    assert await store.get_dynamic_config('persona_config') == current
    assert next(item for item in await store.list_voice_examples() if item['id'] == manual['id'])['content'] == '改过的人工样例'


@pytest.mark.asyncio
async def test_preset_requires_real_materials_and_rechecks_disabled_asset(persona_store):
    store, service, _ = persona_store
    preview = await store.preview_diana_persona()
    assert preview['missing_media'] == ['开心', '无语']
    with pytest.raises(ValueError, match='运营素材'):
        await store.apply_diana_persona(999, preview['preview_token'])
    assert not await store.list_voice_examples() and await store.get_dynamic_config(PRESET_ID) is None
    refs = await palette(service)
    preview = await store.preview_diana_persona()
    await service.edit(refs['celebrate'], 'global-safe', '开心', ['开心'], False)
    with pytest.raises(ValueError, match='重新预览'):
        await store.apply_diana_persona(999, preview['preview_token'])
    assert not await store.list_voice_examples()


@pytest.mark.asyncio
async def test_voice_api_supports_image_only_and_scoped_mixed_examples(persona_store):
    store, service, runtime = persona_store
    public = await service.upload(picture(), 'global-safe', '公开运营素材', [])
    local = await service.upload(picture(), 'group:a', '本群运营素材', [])
    app = FastAPI()
    app.state.runtime = runtime
    app.include_router(voice_router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        body = {'segments': [{'type': 'image', 'asset_id': public['id']}], 'context': '群友分享喜讯'}
        assert (await client.post('/api/voice/exemplars', json=body)).status_code == 401
        app.dependency_overrides[get_current_user] = lambda: 'operator:test'
        forbidden = await client.post('/api/voice/exemplars', json={'segments': [{'type': 'image', 'asset_id': local['id']}]})
        assert forbidden.status_code == 400 and not await store.list_voice_examples()
        created = await client.post('/api/voice/exemplars', json=body)
        assert created.status_code == 200
        example = created.json()['exemplar']
        assert example['content'] == '[图片]' and example['segments'] == body['segments']
        mixed = {'scene_id': 'group:a', 'context': '先回应再附图', 'segments': [{'type': 'text', 'text': '好耶'}, {'type': 'image', 'asset_id': local['id']}]}
        assert (await client.put('/api/voice/exemplars/' + example['id'], json=mixed)).status_code == 200
        assert not await store.select_voice_examples('group:b')
        selected = await store.select_voice_examples('group:a')
        assert selected[0]['segments'] == mixed['segments'] and selected[0]['content'] == '好耶[图片]'
        await service.edit(local['id'], 'group:a', '本群运营素材', [], False)
        assert not await store.select_voice_examples('group:a')
        listed = (await client.get('/api/voice/exemplars')).json()['exemplars']
        assert listed[0]['available'] is False and listed[0]['segments'] == mixed['segments']
        assert (await client.post('/api/voice/exemplars/toggle', json={'example_id': example['id'], 'enabled': True})).status_code == 400
        assert (await client.delete('/api/voice/exemplars/' + example['id'])).status_code == 200
        assert not await store.list_voice_examples()


@pytest.mark.asyncio
async def test_existing_plain_text_examples_migrate_without_changing_operator_content(tmp_path):
    path = tmp_path / 'old.db'
    content = '  人工写的\n原句「保留」  '
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE voice_exemplars(id TEXT PRIMARY KEY,scene_id TEXT NOT NULL DEFAULT '',
            context TEXT NOT NULL DEFAULT '',content TEXT NOT NULL,tag TEXT NOT NULL DEFAULT '',enabled INTEGER NOT NULL DEFAULT 1,
            use_count INTEGER NOT NULL DEFAULT 0,last_used_at REAL NOT NULL DEFAULT 0,created_at REAL NOT NULL)""")
        db.execute("INSERT INTO voice_exemplars(id,content,context,created_at) VALUES('manual',?,'人工语境',123)", (content,))
    store = EventStore(str(path))
    await store.initialize()
    try:
        row = (await store.list_voice_examples())[0]
        assert row['content'] == content and row['segments'] == [{'type': 'text', 'text': content}]
        assert row['context'] == '人工语境' and row['source'] == 'operator'
        assert (await store.select_voice_examples('group:a'))[0]['id'] == 'manual'
    finally:
        await store.close()
