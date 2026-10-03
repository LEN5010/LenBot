"""Local memory interface: pending material is visible manually, not selected automatically."""
import asyncio

import pytest

from len_bot.next.memory.local import LocalMemory, LocalMemorySettings


def test_pending_files_do_not_take_automatic_search_slots(tmp_path):
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        scene = 'group:80001'
        for n in range(3):
            await backend.write(scene, f'legacy-import/{n}.md', '合成同一主题 abc', 'synthetic pending import')
        await backend.write(scene, 'topics/current.md', '合成同一主题 abc', 'synthetic adopted content')
        assert (await backend.search(scene, 'abc', 1))[0].path.startswith('legacy-import/')
        for query in ('abc', '合成'):
            result = await backend.search(scene, query, 1, exclude_pending=True)
            assert [hit.path for hit in result] == ['topics/current.md']
        assert (await backend.read(scene, 'legacy-import/0.md')).content == '合成同一主题 abc'
    asyncio.run(run())


def test_pending_material_cannot_enter_directory_summaries(tmp_path):
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        scene = 'group:80001'
        await backend.write(scene, 'legacy-import/group.md', '合成待确认内容', 'synthetic')
        # An old generated summary remains readable on disk, but cannot propagate into the root.
        await backend.write_summary(scene, 'legacy-import', '旧版本合成摘要', '旧版本合成概览')
        with pytest.raises(ValueError, match='待确认旧记忆'):
            await backend.summary_inputs(scene, 'legacy-import')
        inputs = await backend.summary_inputs(scene, '')
        assert not inputs['directories'] and not inputs['files']
    asyncio.run(run())


def test_changed_profile_is_not_current_and_empty_summary_can_be_removed(tmp_path):
    from len_bot.next.memory.local import scene_overview
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        scene = 'group:80001'
        await backend.write(scene, 'profile.md', '活动固定在周五。', '已确认安排')
        await backend.write_summary(scene, '', '活动时间', '活动固定在周五。')
        assert scene_overview(backend.root, scene) == '活动固定在周五。'
        await backend.write(scene, 'profile.md', '改到周六，周五取消。', '明确更正')
        old = await backend.summary(scene)
        assert old.changed_after is not None and old.overview == '活动固定在周五。'
        assert backend.summary_text_sync(scene) is None
        await backend.delete(scene, 'profile.md', '删除唯一正文')
        assert not (await backend.summary_inputs(scene, ''))['files']
        await backend.clear_summary(scene, '')
        assert (await backend.summary(scene)).overview is None
        assert scene_overview(backend.root, scene) is None
    asyncio.run(run())


def test_stale_child_summary_is_not_an_input_fact(tmp_path):
    async def run():
        backend = LocalMemory(LocalMemorySettings(directory=tmp_path / 'memory'))
        await backend.write('group:80001', 'events/a.md', '周五', '初始')
        await backend.write_summary('group:80001', 'events', '周五', '周五')
        await backend.write('group:80001', 'events/a.md', '改为周六', '更正')
        inputs = await backend.summary_inputs('group:80001', '')
        assert inputs['directories'] == [{'name': 'events', 'abstract': None}]
    asyncio.run(run())
