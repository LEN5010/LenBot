"""One resolver for global and per-scene attention numbers."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EffectiveAttention(BaseModel):
    model_config = ConfigDict(extra='forbid')
    observation_enabled: bool
    observation_interval_seconds: float = Field(gt=0)
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
    enabled, p_inherited = pick('observation_enabled', runtime.attention_observation_enabled)
    window, w_inherited = pick('observation_interval_seconds', runtime.attention_observation_interval_seconds)
    cooldown, k_inherited = pick('keyword_cooldown_seconds', runtime.attention_keyword_cooldown_seconds)
    return EffectiveAttention(
        observation_enabled=enabled, observation_interval_seconds=window,
        keyword_cooldown_seconds=cooldown, focus_seconds=runtime.attention_focus_seconds,
        keywords=list(runtime.attention_keywords),
        inherited={'observation_enabled': p_inherited, 'observation_interval_seconds': w_inherited,
                   'keyword_cooldown_seconds': k_inherited, 'focus_seconds': True, 'keywords': True})


def raise_attention_two_steps(attention: EffectiveAttention) -> dict:
    """Preview enabling periodic observation and halving its configured interval."""
    window = attention.observation_interval_seconds / 2
    cooldown = attention.keyword_cooldown_seconds
    if cooldown:
        cooldown = min(cooldown, max(20.0, cooldown / 2))
    notes = []
    if not attention.observation_enabled:
        notes.append('当前普通消息的周期观察关闭，保存后开启；真实搭话和短时观察仍独立生效')
    if cooldown == attention.keyword_cooldown_seconds:
        notes.append('关键词冷却未再缩短')
    current_density = 3600 / attention.observation_interval_seconds if attention.observation_enabled else 0.0
    raised_density = 3600 / window
    return {
        'observation_enabled': True,
        'observation_interval_seconds': window,
        'keyword_cooldown_seconds': cooldown,
        'notes': notes,
        'enables_observation': not attention.observation_enabled,
        'density': {
            'current_per_hour': current_density,
            'raised_per_hour': raised_density,
            'unit': '普通观察间隔折算频率，不是总调用上限；实际成本包含快速搭话、分批续读、工具轮次和失败调用',
        },
    }


def effective_sticker_preference(root, scene_id=None) -> str:
    scene = root.scenes.get(scene_id) if scene_id else None
    preference = scene.expression.sticker_preference if scene and scene.expression else None
    return preference or 'natural'
