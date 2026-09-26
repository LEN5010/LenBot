"""Validation of the isolated lab configuration and role-package formats."""

import json
from datetime import time as WallTime

import pytest
from pydantic import ValidationError

from len_bot.next.config import ONEBOT_SETTINGS, LabConfig, OneBotForward, OneBotReverse, QuietHours, load_config
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


@pytest.mark.parametrize(
    ("source", "expected_type"),
    [
        ({"mode": "forward_ws", "ws_url": "wss://example.test:6700/onebot/v11"}, OneBotForward),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 0}, OneBotReverse),
        ({"mode": "forward_ws", "ws_url": "ws://127.0.0.1:6700/events",
          "action_transport": "http", "http_url": "https://example.test:6701/api/v1",
          "access_token": " synthetic-token ", "request_timeout_seconds": 3.5,
          "ping_interval_seconds": 4.5, "ping_timeout_seconds": 2.5,
          "max_frame_bytes": 2048}, OneBotForward),
        ({"mode": "reverse_ws", "listen_host": "::1", "listen_port": 65535,
          "action_transport": "http", "http_url": "http://127.0.0.1:6701/api"}, OneBotReverse),
    ],
)
def test_onebot_transport_configuration_parses_and_roundtrips(source, expected_type):
    settings = ONEBOT_SETTINGS.validate_json(json.dumps(source))

    assert isinstance(settings, expected_type)
    for name, value in source.items():
        assert getattr(settings, name) == value
    assert ONEBOT_SETTINGS.validate_json(settings.model_dump_json()) == settings
    assert settings.action_transport in {"websocket", "http"}
    assert settings.request_timeout_seconds > 0
    assert settings.max_frame_bytes > 0


def test_onebot_transport_defaults_and_can_be_selected_by_lab_root(tmp_path):
    settings = ONEBOT_SETTINGS.validate_python({"mode": "forward_ws", "ws_url": "ws://example.test"})
    assert settings.action_transport == "websocket" and settings.http_url is None
    assert settings.access_token == ""
    assert settings.request_timeout_seconds == 10
    assert settings.ping_interval_seconds == 20 and settings.ping_timeout_seconds == 20
    assert settings.max_frame_bytes == 1048576

    root = tmp_path / "lab"
    source = _config("personas/example")
    source["onebot"] = settings.model_dump()
    _write_config(root, source)
    config = load_config(root)
    assert config.onebot == settings
    assert config.delivery == "simulated"


@pytest.mark.parametrize(
    ("source", "field"),
    [
        ({"mode": "forward_ws"}, "ws_url"),
        ({"mode": "forward_ws", "ws_url": "http://example.test/ws"}, "ws_url"),
        ({"mode": "forward_ws", "ws_url": "ws:///events"}, "ws_url"),
        ({"mode": "forward_ws", "ws_url": "ws://user:secret@example.test/events"}, "ws_url"),
        ({"mode": "forward_ws", "ws_url": "wss://example.test/events?token=x"}, "ws_url"),
        ({"mode": "forward_ws", "ws_url": "wss://example.test/events#fragment"}, "ws_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "listen_port": 9000}, "listen_port"),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1"}, "listen_port"),
        ({"mode": "reverse_ws", "listen_host": " ", "listen_port": 0}, "listen_host"),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": -1}, "listen_port"),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 65536}, "listen_port"),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": True}, "listen_port"),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 6700,
          "ws_url": "ws://example.test"}, "ws_url"),
        ({"mode": "unknown", "ws_url": "ws://example.test"}, "mode"),
        ({"ws_url": "ws://example.test"}, "mode"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "action_transport": "http"}, "http_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "action_transport": "http",
          "http_url": "ws://example.test/actions"}, "http_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "action_transport": "http",
          "http_url": "http:///actions"}, "http_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "action_transport": "http",
          "http_url": "http://user:secret@example.test/actions"}, "http_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "action_transport": "http",
          "http_url": "https://example.test/actions?q=x"}, "http_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "action_transport": "http",
          "http_url": "https://example.test/actions#x"}, "http_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test",
          "http_url": "http://example.test/actions"}, "http_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "action_transport": "invalid"},
         "action_transport"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "request_timeout_seconds": 0},
         "request_timeout_seconds"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "request_timeout_seconds": float("nan")},
         "request_timeout_seconds"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "ping_interval_seconds": float("inf")},
         "ping_interval_seconds"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "ping_timeout_seconds": "20"},
         "ping_timeout_seconds"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "ping_timeout_seconds": 0},
         "ping_timeout_seconds"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "max_frame_bytes": 0},
         "max_frame_bytes"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "max_frame_bytes": "2048"},
         "max_frame_bytes"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "max_frame_bytes": True},
         "max_frame_bytes"),
    ],
)
def test_onebot_transport_configuration_rejects_invalid_input(source, field):
    with pytest.raises(ValidationError) as failure:
        ONEBOT_SETTINGS.validate_python(source)
    assert field in str(failure.value)


def test_onebot_transport_token_is_preserved_but_not_rendered_in_errors_or_repr():
    settings = ONEBOT_SETTINGS.validate_python({"mode": "forward_ws", "ws_url": "ws://example.test",
                                                "access_token": " synthetic-token "})
    assert settings.access_token == " synthetic-token "
    assert "synthetic-token" not in repr(settings)

    with pytest.raises(ValidationError) as failure:
        ONEBOT_SETTINGS.validate_python({"mode": "forward_ws", "ws_url": "ws://example.test",
                                         "access_token": "synthetic-token\r\nInjected: true"})
    assert "access_token" in str(failure.value)
    assert "synthetic-token" not in str(failure.value)

    with pytest.raises(ValidationError) as json_failure:
        ONEBOT_SETTINGS.validate_json(json.dumps({"mode": "forward_ws", "ws_url": "ws://example.test",
                                                   "access_token": "synthetic-token\nInjected: true"}))
    assert "synthetic-token" not in str(json_failure.value)


@pytest.mark.parametrize(
    ("onebot", "delivery", "expected_type"),
    [
        ({"mode": "forward_ws", "ws_url": "wss://example.test:6700/events"},
         "simulated", OneBotForward),
        ({"mode": "forward_ws", "ws_url": "ws://127.0.0.1:6700/events"},
         "onebot", OneBotForward),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 0,
          "action_transport": "http", "http_url": "http://127.0.0.1:6701/actions",
          "access_token": "synthetic-onebot-token"}, "onebot", OneBotReverse),
    ],
)
def test_lab_root_selects_typed_onebot_transport_and_delivery(tmp_path, onebot, delivery, expected_type):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source.update(onebot=onebot, delivery=delivery)
    _write_config(root, source)

    config = load_config(root)

    assert isinstance(config.onebot, expected_type)
    assert config.delivery == delivery
    for field, value in onebot.items():
        assert getattr(config.onebot, field) == value
    assert LabConfig.model_validate_json(config.model_dump_json()) == config
    assert "synthetic-onebot-token" not in repr(config)


@pytest.mark.parametrize(
    ("onebot", "delivery", "field"),
    [
        (None, "onebot", "delivery=onebot requires onebot"),
        (None, "network", "delivery"),
        ({"mode": "forward_ws"}, "simulated", "onebot.forward_ws.ws_url"),
        ({"mode": "forward_ws", "ws_url": "http://example.test/events"},
         "onebot", "onebot.forward_ws.ws_url"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test", "listen_port": 6700},
         "onebot", "onebot.forward_ws.listen_port"),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 6700,
          "ws_url": "ws://example.test"}, "onebot", "onebot.reverse_ws.ws_url"),
        ({"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 6700,
          "action_transport": "http"}, "onebot", "http_url"),
        ({"mode": "unknown", "ws_url": "ws://example.test"}, "onebot", "onebot"),
        ({"mode": "forward_ws", "ws_url": "ws://example.test",
          "access_token": "synthetic-onebot-token\r\nInjected: yes"}, "onebot", "access_token"),
    ],
)
def test_lab_root_rejects_invalid_onebot_and_delivery_without_token_echo(tmp_path, onebot, delivery, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source.update(onebot=onebot, delivery=delivery)
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-onebot-token" not in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


def test_isolated_config_resolves_paths_and_explicit_model_bindings(tmp_path):
    root = tmp_path / "lab"
    external_persona = tmp_path / "operator-persona"
    _write_config(root, _config(str(external_persona)))

    config = load_config(root)

    assert config.database == root / "data/isolated-chat.db"
    assert config.persona == external_persona
    assert config.voice_mode == "voice" and config.max_steps == 8
    assert config.onebot is None and config.delivery == "simulated"
    assert config.compaction.trigger_ratio == 0.6
    assert config.compaction.keep_recent_entries == 30
    assert config.compaction.max_output_tokens == 1024
    assert config.attention.direct_idle_seconds == 1.5
    assert config.attention.direct_max_seconds == 4.0
    assert config.attention.max_extensions == 2
    assert config.attention.only_direct is False
    assert config.attention.keywords == []
    assert config.attention.other_bot_qqs == []
    assert config.attention.quiet_hours is None
    assert (config.attention.named_idle_seconds, config.attention.named_max_seconds) == (3.0, 8.0)
    assert config.attention.keyword_cooldown_seconds == 60.0
    assert (config.attention.focus_seconds, config.attention.focus_idle_seconds,
            config.attention.focus_max_seconds) == (180.0, 4.0, 12.0)
    assert (config.attention.activity, config.attention.ambient_threshold) == (0.3, 0.5)
    assert (config.attention.ambient_min_interval_seconds,
            config.attention.ambient_max_interval_seconds) == (60.0, 900.0)
    assert config.schedules.enabled is True and config.schedules.max_pending == 50
    assert config.schedules.owner is None
    assert config.schedules.admins == [] and config.schedules.whitelist == []
    assert config.schedules.own == ["owner", "admin", "group_manager", "whitelist", "member"]
    assert config.schedules.others == ["owner", "admin", "group_manager"]
    assert config.schedules.manage == ["owner", "admin", "group_manager"]
    assert config.schedules.autonomous is True
    assert config.models.roles.mind.context_window_tokens == 8192
    assert config.models.roles.voice.context_window_tokens == 4096
    assert config.model_settings("mind").model == "sample-mind"
    assert config.model_settings("mind").reasoning_effort == "high"
    assert config.model_settings("voice").model == "sample-voice"
    assert "context_window_tokens" not in config.model_settings("mind").model_dump()
    assert "synthetic-secret-marker" not in repr(config)


@pytest.mark.parametrize(
    "schedules",
    [
        {
            "enabled": False,
            "max_pending": 7,
            "owner": "80002",
            "admins": ["80003"],
            "whitelist": ["80003", "80004"],
            "own": ["owner", "admin", "member"],
            "others": ["admin", "group_manager"],
            "manage": ["owner", "whitelist"],
            "autonomous": False,
        },
        {"owner": None, "admins": [], "whitelist": [], "own": [], "others": [], "manage": []},
    ],
)
def test_schedule_configuration_accepts_explicit_permissions_and_roundtrips(tmp_path, schedules):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["schedules"] = schedules
    _write_config(root, source)

    config = load_config(root)

    for field, value in schedules.items():
        assert getattr(config.schedules, field) == value
    assert LabConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize(
    ("schedules", "field"),
    [
        ({"enabled": "false"}, "enabled"),
        ({"max_pending": 0}, "max_pending"),
        ({"max_pending": "50"}, "max_pending"),
        ({"owner": "0"}, "owner"),
        ({"owner": "080002"}, "owner"),
        ({"owner": 80002}, "owner"),
        ({"admins": ["0"]}, "admins"),
        ({"admins": [80003]}, "admins"),
        ({"whitelist": ["80004a"]}, "whitelist"),
        ({"whitelist": [80004]}, "whitelist"),
        ({"own": ["owner", "unknown"]}, "own"),
        ({"others": ["member", "member"]}, "others"),
        ({"manage": ["admin", "admin"]}, "manage"),
        ({"owner": "90001"}, "bot_qq"),
        ({"admins": ["90001"]}, "bot_qq"),
        ({"whitelist": ["90001"]}, "bot_qq"),
    ],
)
def test_schedule_configuration_rejects_invalid_permissions(tmp_path, schedules, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["schedules"] = schedules
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


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
    ("quiet", "expected_direct", "expected_notice"),
    [
        ({"start": "23:30", "end": "07:15:30", "notice_text": None}, "defer", None),
        ({"start": "01:00:00", "end": "08:00", "direct": "allow",
          "notice_text": None}, "allow", None),
        ({"start": "22:00", "end": "06:00", "direct": "notice",
          "notice_text": "合成时段说明"}, "notice", "合成时段说明"),
    ],
)
def test_quiet_hours_parse_explicit_local_clocks(tmp_path, quiet, expected_direct, expected_notice):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["attention"] = {"quiet_hours": quiet}
    _write_config(root, source)

    settings = load_config(root).attention.quiet_hours
    assert settings.start == WallTime.fromisoformat(quiet["start"])
    assert settings.end == WallTime.fromisoformat(quiet["end"])
    assert settings.direct == expected_direct
    assert settings.notice_text == expected_notice
    assert QuietHours.model_validate_json(settings.model_dump_json()) == settings


@pytest.mark.parametrize(
    ("quiet", "field"),
    [
        ({"start": "1:00", "end": "08:00"}, "start"),
        ({"start": "24:00", "end": "08:00"}, "start"),
        ({"start": "01:00:60", "end": "08:00"}, "start"),
        ({"start": "01:00.000", "end": "08:00"}, "start"),
        ({"start": "01:00+08:00", "end": "08:00"}, "start"),
        ({"start": 100, "end": "08:00"}, "start"),
        ({"start": "01:00", "end": "01:00:00"}, "start and end must differ"),
        ({"start": "01:00", "end": "08:00", "direct": "notice"}, "notice_text"),
        ({"start": "01:00", "end": "08:00", "direct": "notice",
          "notice_text": "   "}, "notice_text"),
        ({"start": "01:00", "end": "08:00", "direct": "allow",
          "notice_text": "合成说明"}, "notice_text"),
        ({"start": "01:00", "end": "08:00", "direct": "defer",
          "notice_text": "合成说明"}, "notice_text"),
        ({"start": "01:00", "end": "08:00", "direct": "sleep"}, "direct"),
        ({"start": "01:00", "end": "08:00", "unknown": True}, "unknown"),
    ],
)
def test_quiet_hours_reject_invalid_configuration(tmp_path, quiet, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["attention"] = {"quiet_hours": quiet}
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


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
