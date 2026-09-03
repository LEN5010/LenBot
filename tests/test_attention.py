import time
from len_bot.config import RuntimeConfig
from len_bot.events.models import Stimulus, StimulusType
from len_bot.scenes.models import SceneState
from len_bot.attention.models import AttentionDisposition
from len_bot.attention.engine import AttentionEngine

def test_hard_attention_mention():
    config = RuntimeConfig()
    engine = AttentionEngine(config)
    st = Stimulus(
        scene_id="group:1",
        stimulus_type=StimulusType.SINGLE_MESSAGE,
        source_event_ids=["1"],
        actor_id="user:A",
        combined_text="@Bot 你看吗",
        has_mention_bot=True
    )
    res = engine.evaluate(st, None, [])
    assert res.disposition == AttentionDisposition.WAKE
    assert "mention" in res.reason

def test_heuristic_attention_keywords_and_cooldown():
    config = RuntimeConfig(monitored_keywords=["直播"])
    engine = AttentionEngine(config)
    
    # 1. User says "今晚直播有人看吗", Bot never spoke -> WAKE
    st = Stimulus(
        scene_id="group:1",
        stimulus_type=StimulusType.SINGLE_MESSAGE,
        source_event_ids=["1"],
        actor_id="user:A",
        combined_text="今晚直播有人看吗"
    )
    res = engine.evaluate(st, None, [])
    assert res.disposition == AttentionDisposition.WAKE

    # 2. Scene shows Bot spoke 10 seconds ago (cooldown active) -> TRACK only
    now = time.time()
    state = SceneState(scene_id="group:1", recent_bot_message_at=now - 10)
    res2 = engine.evaluate(st, state, [])
    assert res2.disposition == AttentionDisposition.TRACK
    assert "cooldown" in res2.reason

def test_attention_observe_on_unrelated():
    config = RuntimeConfig()
    engine = AttentionEngine(config)
    st = Stimulus(
        scene_id="group:1",
        stimulus_type=StimulusType.SINGLE_MESSAGE,
        source_event_ids=["1"],
        actor_id="user:A",
        combined_text="今天午饭吃了黄焖鸡"
    )
    res = engine.evaluate(st, None, [])
    assert res.disposition == AttentionDisposition.OBSERVE
