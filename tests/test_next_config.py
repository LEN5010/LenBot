"""Validation of the isolated lab configuration and role-package formats."""

import json

import pytest

from len_bot.next.config import load_config
from len_bot.next.persona import load_persona


def _config(persona: str) -> dict:
    return {
        "mode": "isolated",
        "scene": "group:80001",
        "bot_qq": "90001",
        "timezone": "Asia/Shanghai",
        "database": "data/isolated-chat.db",
        "persona": persona,
        "models": {
            "providers": {
                "sample": {
                    "api": "openai-chat",
                    "base_url": "http://127.0.0.1:18080/v1",
                    "api_key": "synthetic-secret-marker",
                }
            },
            "roles": {
                "mind": {"provider": "sample", "model": "sample-mind", "reasoning_effort": "high",
                         "context_window_tokens": 8192},
                "voice": {"provider": "sample", "model": "sample-voice", "context_window_tokens": 4096},
            },
        },
    }


def _write_config(root, source: dict) -> None:
    root.mkdir()
    (root / "lenbot.config.json").write_text(json.dumps(source), encoding="utf-8")


def test_isolated_config_resolves_paths_and_explicit_model_bindings(tmp_path):
    root = tmp_path / "lab"
    external_persona = tmp_path / "operator-persona"
    _write_config(root, _config(str(external_persona)))

    config = load_config(root)

    assert config.database == root / "data/isolated-chat.db"
    assert config.persona == external_persona
    assert config.voice_mode == "voice" and config.max_steps == 8
    assert config.compaction.trigger_ratio == 0.6
    assert config.compaction.keep_recent_entries == 30
    assert config.compaction.max_output_tokens == 1024
    assert config.attention.direct_idle_seconds == 1.5
    assert config.attention.direct_max_seconds == 4.0
    assert config.attention.max_extensions == 2
    assert config.attention.only_direct is False
    assert config.attention.keywords == []
    assert config.attention.other_bot_qqs == []
    assert (config.attention.named_idle_seconds, config.attention.named_max_seconds) == (3.0, 8.0)
    assert config.attention.keyword_cooldown_seconds == 60.0
    assert (config.attention.focus_seconds, config.attention.focus_idle_seconds,
            config.attention.focus_max_seconds) == (180.0, 4.0, 12.0)
    assert (config.attention.activity, config.attention.ambient_threshold) == (0.3, 0.5)
    assert (config.attention.ambient_min_interval_seconds,
            config.attention.ambient_max_interval_seconds) == (60.0, 900.0)
    assert config.models.roles.mind.context_window_tokens == 8192
    assert config.models.roles.voice.context_window_tokens == 4096
    assert config.model_settings("mind").model == "sample-mind"
    assert config.model_settings("mind").reasoning_effort == "high"
    assert config.model_settings("voice").model == "sample-voice"
    assert "context_window_tokens" not in config.model_settings("mind").model_dump()
    assert "synthetic-secret-marker" not in repr(config)


def test_isolated_attention_accepts_explicit_short_direct_timing(tmp_path):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["attention"] = {
        "only_direct": True,
        "keywords": ["  然然  ", "开播"],
        "other_bot_qqs": ["90002"],
        "direct_idle_seconds": 0.02,
        "direct_max_seconds": 0.05,
        "named_idle_seconds": 0.03,
        "named_max_seconds": 0.06,
        "keyword_cooldown_seconds": 0.01,
        "focus_seconds": 0.2,
        "focus_idle_seconds": 0.04,
        "focus_max_seconds": 0.08,
        "activity": 0.8,
        "ambient_threshold": 0.2,
        "ambient_min_interval_seconds": 0.05,
        "ambient_max_interval_seconds": 0.1,
        "max_extensions": 0,
    }
    _write_config(root, source)

    attention = load_config(root).attention
    assert (attention.direct_idle_seconds, attention.direct_max_seconds, attention.max_extensions) == (0.02, 0.05, 0)
    assert attention.only_direct is True
    assert attention.keywords == ["然然", "开播"] and attention.other_bot_qqs == ["90002"]
    assert (attention.named_idle_seconds, attention.named_max_seconds) == (0.03, 0.06)
    assert (attention.focus_seconds, attention.focus_idle_seconds, attention.focus_max_seconds) == (0.2, 0.04, 0.08)
    assert (attention.ambient_min_interval_seconds, attention.ambient_max_interval_seconds) == (0.05, 0.1)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda source: source.update(mode="production"), "mode"),
        (lambda source: source.update(scene="room:80001"), "scene"),
        (lambda source: source.update(timezone="Invalid/Timezone"), "timezone"),
        (lambda source: source.update(database="../legacy.db"), "database"),
        (lambda source: source.update(max_steps=0), "max_steps"),
        (lambda source: source.update(extra_runtime_flag=True), "extra_runtime_flag"),
        (lambda source: source["models"]["roles"]["voice"].update(provider="missing"), "voice.provider"),
        (lambda source: source["models"]["roles"]["mind"].update(reasoning_effort=" "), "reasoning_effort"),
        (lambda source: source["models"]["roles"]["mind"].pop("context_window_tokens"), "context_window_tokens"),
        (lambda source: source["models"]["roles"]["voice"].update(context_window_tokens=0), "context_window_tokens"),
        (lambda source: source["models"]["roles"]["voice"].update(context_window_tokens=True), "context_window_tokens"),
        (lambda source: source["models"]["roles"]["voice"].update(max_output_tokens=4096), "max_output_tokens"),
        (lambda source: source["models"]["roles"]["mind"].update(max_output_tokens=5000), "compaction.trigger_ratio"),
        (lambda source: source.update(compaction={"trigger_ratio": 0.1}), "compaction.trigger_ratio"),
        (lambda source: source.update(compaction={"trigger_ratio": 1}), "trigger_ratio"),
        (lambda source: source.update(compaction={"trigger_ratio": "0.6"}), "trigger_ratio"),
        (lambda source: source.update(compaction={"keep_recent_entries": 0}), "keep_recent_entries"),
        (lambda source: source.update(compaction={"keep_recent_entries": 30.0}), "keep_recent_entries"),
        (lambda source: source.update(compaction={"max_output_tokens": 0}), "max_output_tokens"),
        (lambda source: source.update(compaction={"max_output_tokens": 100000}), "compaction.max_output_tokens"),
        (lambda source: source.update(compaction={"unknown": True}), "unknown"),
        (lambda source: source.update(attention={"direct_idle_seconds": -0.1}), "direct_idle_seconds"),
        (lambda source: source.update(attention={"direct_idle_seconds": True}), "direct_idle_seconds"),
        (lambda source: source.update(attention={"direct_idle_seconds": "0.1"}), "direct_idle_seconds"),
        (lambda source: source.update(attention={"direct_max_seconds": 0}), "direct_max_seconds"),
        (lambda source: source.update(attention={"direct_max_seconds": "4.0"}), "direct_max_seconds"),
        (lambda source: source.update(attention={"direct_max_seconds": float("inf")}), "direct_max_seconds"),
        (lambda source: source.update(attention={"direct_idle_seconds": 5.0}), "direct_idle_seconds"),
        (lambda source: source.update(attention={"max_extensions": -1}), "max_extensions"),
        (lambda source: source.update(attention={"max_extensions": True}), "max_extensions"),
        (lambda source: source.update(attention={"max_extensions": "2"}), "max_extensions"),
        (lambda source: source.update(attention={"ambient_idle_seconds": 1.0}), "ambient_idle_seconds"),
        (lambda source: source.update(attention={"only_direct": "false"}), "only_direct"),
        (lambda source: source.update(attention={"keywords": ["  "]}), "keywords"),
        (lambda source: source.update(attention={"keywords": ["然然", " 然然 "]}), "keywords"),
        (lambda source: source.update(attention={"keywords": [123]}), "keywords"),
        (lambda source: source.update(attention={"other_bot_qqs": ["0"]}), "other_bot_qqs"),
        (lambda source: source.update(attention={"other_bot_qqs": ["abc"]}), "other_bot_qqs"),
        (lambda source: source.update(attention={"other_bot_qqs": [90002]}), "other_bot_qqs"),
        (lambda source: source.update(attention={"named_idle_seconds": -1}), "named_idle_seconds"),
        (lambda source: source.update(attention={"named_idle_seconds": 9}), "named_idle_seconds"),
        (lambda source: source.update(attention={"named_max_seconds": 0}), "named_max_seconds"),
        (lambda source: source.update(attention={"keyword_cooldown_seconds": -1}), "keyword_cooldown_seconds"),
        (lambda source: source.update(attention={"focus_seconds": -1}), "focus_seconds"),
        (lambda source: source.update(attention={"focus_idle_seconds": 13}), "focus_idle_seconds"),
        (lambda source: source.update(attention={"focus_max_seconds": 0}), "focus_max_seconds"),
        (lambda source: source.update(attention={"activity": -0.1}), "activity"),
        (lambda source: source.update(attention={"activity": 1.1}), "activity"),
        (lambda source: source.update(attention={"activity": True}), "activity"),
        (lambda source: source.update(attention={"ambient_threshold": 0}), "ambient_threshold"),
        (lambda source: source.update(attention={"ambient_min_interval_seconds": 0}), "ambient_min_interval_seconds"),
        (lambda source: source.update(attention={"ambient_max_interval_seconds": 0}), "ambient_max_interval_seconds"),
        (lambda source: source.update(attention={"ambient_min_interval_seconds": 901}), "ambient_min_interval_seconds"),
        (lambda source: source.update(attention={"ambient_max_interval_seconds": "1"}), "ambient_max_interval_seconds"),
        (lambda source: source.update(attention={"focus_seconds": float("inf")}), "focus_seconds"),
    ],
)
def test_invalid_lab_configuration_names_field_without_leaking_key(tmp_path, change, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    change(source)
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


def test_persona_package_keeps_explicit_fields_and_selects_first_eight_examples(tmp_path):
    path = tmp_path / "example"
    path.mkdir()
    (path / "persona.yaml").write_text(
        """id: example
name: 示例角色
aliases: [小例]
self_reference: [我]
brief: 在隔离试聊中使用的示例身份。
behavior: 先听清问题，再简短回答。
styles:
  - name: 日常
    weight: 0.8
  - name: 活泼
    weight: 0.2
    note: 语气轻快
tools: all
skills: []
""",
        encoding="utf-8",
    )
    (path / "voice.md").write_text("短句，清楚。", encoding="utf-8")
    (path / "boundaries.md").write_text("不声称拥有真实经历。", encoding="utf-8")
    (path / "examples.yaml").write_text(
        "".join(f"- context: 情境 {number}\n  line: 台词 {number}\n" for number in range(9)),
        encoding="utf-8",
    )

    persona = load_persona(path)

    assert persona.name == "示例角色"
    assert persona.tools == "all" and persona.skills == []
    assert persona.styles[1].note == "语气轻快"
    assert persona.voice == "短句，清楚。"
    assert persona.boundaries == "不声称拥有真实经历。"
    assert [example.line for example in persona.examples] == [f"台词 {number}" for number in range(8)]


def test_persona_rejects_blank_alias_at_package_boundary(tmp_path):
    path = tmp_path / "example"
    path.mkdir()
    (path / "persona.yaml").write_text(
        "id: example\nname: 示例角色\nbrief: 示例\nbehavior: 示例\n"
        "self_reference: [我]\naliases: ['   ']\ntools: []\nskills: []\nstyles: []\n",
        encoding="utf-8",
    )
    (path / "voice.md").write_text("示例", encoding="utf-8")
    (path / "boundaries.md").write_text("示例", encoding="utf-8")
    (path / "examples.yaml").write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="aliases"):
        load_persona(path)
