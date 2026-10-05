"""Run the example through the public local plugin harness."""

import json
from pathlib import Path

import pytest

from len_bot.next.plugin_testing import PluginTest


@pytest.mark.asyncio
async def test_counter_keeps_scenes_separate_and_uses_saved_step():
    scenes = ('onebot:group:80001', 'onebot:group:80002')
    async with PluginTest(Path(__file__).parents[1], config={'step': 2}, scenes=scenes) as bot:
        assert await bot.message('计数加一', scene=scenes[0])
        assert bot.deliveries[-1].text == '本群计数：2'
        assert bot.deliveries[-1].status == 'simulated'
        assert json.loads(await bot.tool('counter_read', {}, scene=scenes[1]))['count'] == 0
        assert await bot.message('/计数清零', scene=scenes[0])
        assert json.loads(await bot.tool('counter_read', {}, scene=scenes[0]))['count'] == 0
