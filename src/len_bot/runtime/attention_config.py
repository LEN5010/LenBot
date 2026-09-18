"""One resolver for global and per-scene attention numbers."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EffectiveAttention(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sample_probability: float = Field(ge=0, le=1)
    sample_window_seconds: float = Field(gt=0)
    keyword_cooldown_seconds: float = Field(ge=0)
    focus_seconds: float = Field(gt=0)
    keywords: list[str]
    inherited: dict[str, bool]


def _scene_attention(root, scene_id):
    scene = root.scenes.get(scene_id) if scene_id else None
    return scene.attention if scene is not None else None


def effective_attention(root, scene_id=None) -> EffectiveAttention:
    runtime = root.runtime
    override = _scene_attention(root, scene_id)
    def pick(name, global_value):
        value = getattr(override, name, None) if override is not None else None
        return global_value if value is None else value, value is None
    probability, p_inherited = pick('sample_probability', runtime.attention_sample_probability)
    window, w_inherited = pick('sample_window_seconds', runtime.attention_sample_window_seconds)
    cooldown, k_inherited = pick('keyword_cooldown_seconds', runtime.attention_keyword_cooldown_seconds)
    return EffectiveAttention(
        sample_probability=probability, sample_window_seconds=window,
        keyword_cooldown_seconds=cooldown, focus_seconds=runtime.attention_focus_seconds,
        keywords=list(runtime.attention_keywords),
        inherited={'sample_probability': p_inherited, 'sample_window_seconds': w_inherited,
                   'keyword_cooldown_seconds': k_inherited, 'focus_seconds': True, 'keywords': True})


def raise_attention_two_steps(attention: EffectiveAttention) -> dict:
    """Concrete +2 preview from the current effective numbers, not a stored multiplier.

    The probability is a switch now rather than a draw, so raising it only ever
    turns a silent room back on. The interval is the real lever: halving it
    doubles how often the room is read.
    """
    probability = min(1.0, attention.sample_probability + 0.20)
    window = min(attention.sample_window_seconds, max(30.0, attention.sample_window_seconds / 2))
    cooldown = attention.keyword_cooldown_seconds
    if cooldown:
        cooldown = min(cooldown, max(20.0, cooldown / 2))
    notes = []
    if attention.sample_probability == 0 and probability > 0:
        notes.append('当前该场景完全不观察，保存后将按间隔开始观察')
    if window == attention.sample_window_seconds:
        notes.append('观察间隔未再缩短（已低于或等于 30 秒，或不需要减半）')
    if cooldown == attention.keyword_cooldown_seconds:
        notes.append('关键词冷却未再缩短')
    current_density = 3600 / attention.sample_window_seconds if attention.sample_probability else 0.0
    raised_density = 3600 / window if probability else 0.0
    return {
        'sample_probability': probability,
        'sample_window_seconds': window,
        'keyword_cooldown_seconds': cooldown,
        'notes': notes,
        'enables_sampling': attention.sample_probability == 0 and probability > 0,
        'density': {
            'current_per_hour': current_density,
            'raised_per_hour': raised_density,
            'unit': '每小时主动观察次数上限，实际模型轮次仍受合并、睡眠和预算影响',
        },
    }


def effective_sticker_preference(root, scene_id=None) -> str:
    scene = root.scenes.get(scene_id) if scene_id else None
    preference = scene.expression.sticker_preference if scene and scene.expression else None
    return preference or 'natural'
