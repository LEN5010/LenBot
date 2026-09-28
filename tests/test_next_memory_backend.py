"""Local memory interface: pending material is visible manually, not selected automatically."""
import asyncio

import pytest

from len_bot.next.memory_local import LocalMemory, LocalMemorySettings


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
