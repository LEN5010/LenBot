import pytest
import asyncio
import time
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType, Stimulus, StimulusType
from len_bot.events.builder import StimulusBuilder

@pytest.mark.asyncio
async def test_stimulus_debounce_and_burst():
    received_stimuli: list[Stimulus] = []
    
    async def on_stimulus(st: Stimulus):
        received_stimuli.append(st)

    config = RuntimeConfig(debounce_idle_ms=100, debounce_max_ms=500)
    builder = StimulusBuilder(config, on_stimulus)

    now = time.time()
    # User A sends 3 consecutive short messages
    e1 = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:1", actor_id="user:A", timestamp=now, payload={"raw_text": "等等"})
    e2 = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:1", actor_id="user:A", timestamp=now + 0.02, payload={"raw_text": "我看看"})
    e3 = Event(event_type=EventType.GROUP_MESSAGE_RECEIVED, scene_id="group:1", actor_id="user:A", timestamp=now + 0.04, payload={"raw_text": "好像八点"})

    await builder.ingest(e1)
    await builder.ingest(e2)
    await builder.ingest(e3)

    # Wait for the 100ms idle timer to fire
    await asyncio.sleep(0.2)

    assert len(received_stimuli) == 1
    st = received_stimuli[0]
    assert st.stimulus_type == StimulusType.SOCIAL_MESSAGE_BURST
    assert st.combined_text == "等等\n我看看\n好像八点"
    assert len(st.source_event_ids) == 3

@pytest.mark.asyncio
async def test_stimulus_immediate_flush_on_mention():
    received_stimuli: list[Stimulus] = []
    
    async def on_stimulus(st: Stimulus):
        received_stimuli.append(st)

    config = RuntimeConfig(debounce_idle_ms=500)
    builder = StimulusBuilder(config, on_stimulus)

    # Immediate flush event: @Bot
    e = Event(
        event_type=EventType.GROUP_MESSAGE_RECEIVED,
        scene_id="group:1",
        actor_id="user:A",
        timestamp=time.time(),
        payload={"raw_text": "@Bot 你在吗", "at_bot": True}
    )
    await builder.ingest(e)

    # Must be emitted synchronously without waiting for idle timer
    assert len(received_stimuli) == 1
    assert received_stimuli[0].has_mention_bot is True
