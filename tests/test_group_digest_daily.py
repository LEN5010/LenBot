"""Daily group summary: time-window read, batched generation, quotes from the store, card output."""

import asyncio
from io import BytesIO
import json
from pathlib import Path
import sys

from PIL import Image as PILImage
import pytest

from len_bot.next.platform.messages import ChatMessage, Segment, Sender
from len_bot.next.plugin import Image, Text
from len_bot.next.plugin_testing import PluginTest

SOURCE = Path(__file__).resolve().parents[1] / 'src/len_bot/next/builtin_plugins/group_digest'
SCENE = 'onebot:group:80001'


def system_font() -> str | None:
    from len_bot.next.builtin_plugins.group_digest.card import find_font
    try:
        return str(find_font(''))
    except ValueError:
        dejavu = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
        return str(dejavu) if dejavu.is_file() else None


FONT = system_font()
pytestmark = pytest.mark.skipif(FONT is None, reason='no font file to draw the card with')


def message(bot: PluginTest, seconds_ago: float, sender: str, name: str, text: str, *, is_self: bool = False,
            status: str = 'received') -> ChatMessage:
    number = str(len(bot.host.messages) + 1)
    item = ChatMessage(id=number, platform='onebot', bot_id='onebot:90001', scene=SCENE, platform_message_id=number,
                       sender=Sender(sender, name, None, None), time=bot.host.now() - seconds_ago,
                       segments=[Segment('text', {'text': text})], reply_to=None, mentions_bot=False,
                       is_self=is_self, send_status=status)
    bot.host.messages.append(item)
    return item


class Model:
    """Answers the chunk and final prompts and records what it was asked."""

    def __init__(self, quotes: list[dict]):
        self.calls: list[tuple[str, dict]] = []
        self.quotes = quotes

    async def __call__(self, plugin, scene, prompt, role, system):
        material = json.loads(prompt)
        kind = 'final' if 'headline' in system else 'chunk'
        self.calls.append((kind, material))
        if kind == 'chunk':
            return json.dumps({'topics': [{'title': '周六桌游', 'summary': '定了六点开局', 'people': ['小满'],
                                           'evidence': [1]}], 'quotes': []})
        return '```json\n' + json.dumps({
            'headline': '周末桌游局基本敲定', 'topics': [
                {'title': '周六桌游', 'summary': '六点在老地方开局，晚到的点外卖。', 'people': ['小满', '不存在的人']},
                {'title': '摄影', 'summary': '有人拍到了火烧云。', 'people': ['老周']}],
            'quotes': self.quotes}, ensure_ascii=False) + '\n```'


@pytest.fixture(autouse=True)
def package(tmp_path, monkeypatch):
    """The harness installs a copy; a built-in name would collide with the built-in itself."""
    import shutil
    target = tmp_path / 'digest_copy'
    shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns('__pycache__'))
    manifest = target / 'plugin.toml'
    manifest.write_text(manifest.read_text().replace('name = "group_digest"', 'name = "digest_copy"', 1))
    monkeypatch.setitem(globals(), 'PACKAGE', target)


def config(**values) -> dict:
    return {'font_path': FONT, **values}


async def finish(bot: PluginTest) -> None:
    while bot.host.tasks:
        await asyncio.gather(*bot.host.tasks, return_exceptions=True)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_tool_returns_at_once_and_sends_a_card_with_checked_quotes():
    async with PluginTest(PACKAGE, config=config()) as bot:
        lines = [message(bot, 3600 * 30, 'onebot:70009', '昨天的人', '这条在窗口外')]
        lines += [message(bot, 3600 * (20 - index), f'onebot:7000{index % 3 + 1}', ['小满', '老周', '橘子'][index % 3],
                          f'第 {index} 条：周六六点老地方') for index in range(8)]
        message(bot, 600, 'onebot:90001', '小然', 'Bot 自己的话', is_self=True, status='sent')
        message(bot, 300, 'onebot:70001', '小满', '没发出去的', status='failed')
        model = Model([{'index': 2, 'comment': '定调了'}, {'index': 999, 'comment': '编号不存在'},
                       {'index': 9, 'comment': 'Bot 的话不选'}, {'index': 2, 'comment': '重复'}])
        bot.host.generate = model
        started = await bot.tool('group_summary_card', {})
        assert '开始生成过去 24 小时' in started
        await finish(bot)
        assert [kind for kind, _ in model.calls] == ['final']
        transcript = model.calls[0][1]['群聊记录']
        assert '这条在窗口外' not in transcript and '没发出去的' not in transcript and '（Bot）' in transcript
        assert model.calls[0][1]['统计']['消息数'] == 9 and model.calls[0][1]['统计']['发言人数'] == 3
        sent, = bot.deliveries
        image, = sent.parts
        assert isinstance(image, Image)
        assert PILImage.open(BytesIO(image.data)).size[0] == 1200
        assert '周末桌游局基本敲定' in image.description and '不存在的人' not in image.description
        assert image.description.count('原话：') == 1 and '第 1 条：周六六点老地方' in image.description


@pytest.mark.asyncio
async def test_long_day_is_read_in_batches_then_merged():
    async with PluginTest(PACKAGE, config=config(batch_chars=2000)) as bot:
        for index in range(120):
            message(bot, 3600 * 20 - index * 60, 'onebot:70001', '小满', f'第 {index} 条 ' + '聊天内容' * 10)
        model = Model([])
        bot.host.generate = model
        assert await bot.message('/群日报')
        await finish(bot)
        kinds = [kind for kind, _ in model.calls]
        assert kinds[-1] == 'final' and kinds.count('chunk') >= 3 and kinds.count('final') == 1
        assert len(model.calls[-1][1]['各段整理']) == kinds.count('chunk')
        assert all(len(material['群聊记录']) <= 2000 for kind, material in model.calls if kind == 'chunk')
        assert isinstance(bot.deliveries[-1].parts[0], Image)


@pytest.mark.asyncio
async def test_quiet_group_gets_a_short_note_and_no_model_call():
    async with PluginTest(PACKAGE, config=config()) as bot:
        message(bot, 100, 'onebot:70001', '小满', '有人吗')
        model = Model([])
        bot.host.generate = model
        assert await bot.message('/群日报 6')
        await finish(bot)
        assert not model.calls
        assert '过去 6 小时群里只有 1 条消息' in bot.deliveries[-1].text


@pytest.mark.asyncio
async def test_bad_hours_and_daily_time_are_reported():
    async with PluginTest(PACKAGE, config=config()) as bot:
        assert await bot.message('/群日报 100')
        assert '1–72' in bot.deliveries[-1].text
    with pytest.raises(RuntimeError, match='22:00'):
        async with PluginTest(PACKAGE, config=config(daily_time='晚上十点', daily_scenes=[SCENE])):
            pass
