import asyncio
import time

import pytest

from len_bot.config import RuntimeConfig
from len_bot.events.builder import BurstAssembler
from len_bot.events.models import Event, EventType, Stimulus, StimulusType


@pytest.mark.asyncio
async def test_burst_assembler_coalesces_same_actor_messages():
    received: list[Stimulus] = []

    async def on_burst(burst: Stimulus) -> None:
        received.append(burst)

    assembler = BurstAssembler(
        RuntimeConfig(debounce_idle_ms=100, debounce_max_ms=500), on_burst
    )
    now = time.time()
    events = [
        Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id="group:1",
            actor_id="user:A",
            timestamp=now + offset,
            payload={"raw_text": text},
        )
        for offset, text in [(0, "等等"), (0.02, "我看看"), (0.04, "好像八点")]
    ]

    for event in events:
        await assembler.ingest(event)
    await asyncio.sleep(0.2)

    assert len(received) == 1
    assert received[0].stimulus_type == StimulusType.SOCIAL_MESSAGE_BURST
    assert received[0].combined_text == "等等\n我看看\n好像八点"
    assert received[0].source_event_ids == [event.id for event in events]
    assert received[0].events == events


@pytest.mark.asyncio
async def test_burst_assembler_groups_a_scene_not_an_actor():
    received: list[Stimulus] = []

    async def on_burst(burst: Stimulus) -> None:
        received.append(burst)

    assembler = BurstAssembler(RuntimeConfig(debounce_idle_ms=500), on_burst)
    now = time.time()
    first = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:A",
        timestamp=now,
        payload={"raw_text": "今晚播吗"},
    )
    second = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:B",
        timestamp=now + 0.01,
        payload={"raw_text": "不知道"},
    )

    await assembler.ingest(first)
    await assembler.ingest(second)
    await assembler.flush_scene("group:1")

    assert len(received) == 1
    assert received[0].source_event_ids == [first.id, second.id]
    assert received[0].combined_text == "user:A: 今晚播吗\nuser:B: 不知道"


@pytest.mark.asyncio
async def test_direct_address_flushes_the_whole_scene_burst():
    received: list[Stimulus] = []

    async def on_burst(burst: Stimulus) -> None:
        received.append(burst)

    assembler = BurstAssembler(RuntimeConfig(debounce_idle_ms=500), on_burst)
    first = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:A",
        payload={"raw_text": "你问他"},
    )
    mention = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:B",
        payload={"raw_text": "@Bot 你觉得呢", "at_bot": True},
    )

    await assembler.ingest(first)
    await assembler.ingest(mention)

    assert len(received) == 1
    assert received[0].source_event_ids == [first.id, mention.id]
    assert received[0].has_mention_bot is True


@pytest.mark.asyncio
async def test_immediate_event_preserves_order_after_pending_chat():
    received: list[Stimulus] = []

    async def on_burst(burst: Stimulus) -> None:
        received.append(burst)

    assembler = BurstAssembler(RuntimeConfig(debounce_idle_ms=500), on_burst)
    chat = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:A",
        payload={"raw_text": "等开播吧"},
    )
    live = Event(
        event_type=EventType.LIVE_STARTED,
        scene_id="group:1",
        actor_id="plugin:bilibili_live",
        payload={"raw_text": "直播开始"},
    )

    await assembler.ingest(chat)
    await assembler.ingest(live)

    assert [burst.source_event_ids for burst in received] == [[chat.id], [live.id]]
    assert received[1].stimulus_type == StimulusType.PLUGIN_FACT
