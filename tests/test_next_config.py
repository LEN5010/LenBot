"""Validation of the isolated lab configuration and role-package formats."""

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from datetime import time as WallTime

import pytest
from pydantic import ValidationError

from len_bot.next.config import (
    ONEBOT_SETTINGS, HistoryExportSettings, HistoryImportSettings, HostConfig, LabConfig, OneBotForward, OneBotReverse,
    PanelSettings, QuietHours, load_config, load_host_config, load_instance_config,
)
from len_bot.next.persona import load_persona
from len_bot.web.auth import hash_password


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


def _host_config() -> dict:
    source = _config("personas/group")
    source["mode"] = "isolated-multi"
    del source["scene"]
    del source["persona"]
    source["onebot"] = {"mode": "reverse_ws", "listen_host": "127.0.0.1", "listen_port": 0}
    source["scenes"] = {
        "group:80001": {
            "persona": "personas/group", "attention": {
                "activity": 0.7, "quiet_hours": {
                    "start": "01:00", "end": "07:30", "direct": "defer",
                },
            },
        },
        "private:80002": {
            "persona": "../private-persona", "voice_mode": "direct",
            "schedules": {"owner": "80002"},
        },
    }
    return source


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
    assert config.panel is None
    assert config.compaction.trigger_ratio == 0.6
    assert config.compaction.keep_recent_entries == 30
    assert config.compaction.max_output_tokens == 1024
    assert config.text_delivery.max_chars == 300
    assert config.text_delivery.min_interval_seconds == 0.6
    assert config.text_delivery.max_interval_seconds == 2.0
    assert config.text_delivery.chars_per_second == 40.0
    assert config.web_read is None
    assert config.images.max_bytes == 10000000
    assert config.images.max_pixels == 25000000
    assert config.images.max_dimension == 1280
    assert config.images.timeout_seconds == 20
    assert config.history_import is None
    assert config.history_export is None
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
    assert config.models.roles.vision is None
    assert config.model_settings("mind").model == "sample-mind"
    assert config.model_settings("mind").reasoning_effort == "high"
    assert config.model_settings("voice").model == "sample-voice"
    with pytest.raises(ValueError, match="models.roles.vision is not configured"):
        config.model_settings("vision")
    assert "context_window_tokens" not in config.model_settings("mind").model_dump()
    assert "synthetic-secret-marker" not in repr(config)


def test_explicit_multiscene_host_roundtrips_and_derives_existing_scene_contract(tmp_path):
    root = tmp_path / "lab"
    _write_config(root, _host_config())

    host = load_host_config(root)

    assert isinstance(host, HostConfig)
    assert host.database == root / "data/isolated-chat.db"
    assert host.bot_qq == "90001" and host.max_model_requests == 4
    assert host.delivery == "simulated"
    assert host.history_import is None
    assert host.history_export is None
    assert isinstance(host.onebot, OneBotReverse)
    assert list(host.scenes) == ["group:80001", "private:80002"]
    assert host.scenes["group:80001"].persona == root / "personas/group"
    assert host.scenes["private:80002"].persona == tmp_path / "private-persona"
    assert HostConfig.model_validate_json(host.model_dump_json()) == host

    group = host.scene_config("group:80001")
    private = host.scene_config("private:80002")
    assert isinstance(group, LabConfig) and isinstance(private, LabConfig)
    assert group.mode == private.mode == "isolated"
    assert group.scene == "group:80001" and private.scene == "private:80002"
    assert group.database == private.database == host.database
    assert group.bot_qq == private.bot_qq == host.bot_qq
    assert group.onebot == private.onebot == host.onebot
    assert group.panel is None and private.panel is None
    assert group.persona == root / "personas/group"
    assert private.persona == tmp_path / "private-persona"
    assert group.attention.activity == 0.7
    assert group.attention.quiet_hours.start == WallTime(1, 0)
    assert private.attention.activity == 0.3
    assert private.voice_mode == "direct" and private.schedules.owner == "80002"
    assert group.model_settings("mind").model == private.model_settings("mind").model == "sample-mind"
    assert group.model_settings("mind").reasoning_effort == "high"
    assert group.model_settings("voice").model == private.model_settings("voice").model == "sample-voice"
    assert LabConfig.model_validate_json(group.model_dump_json()) == group
    assert LabConfig.model_validate_json(private.model_dump_json()) == private
    with pytest.raises(ValueError, match="group:99999.*not configured"):
        host.scene_config("group:99999")
    assert "synthetic-secret-marker" not in repr(host)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda source: source.pop("onebot"), "onebot"),
        (lambda source: source.update(onebot=None), "onebot"),
        (lambda source: source.update(scenes={}), "scenes"),
        (lambda source: source["scenes"].update({"group:0": {"persona": "personas/invalid"}}), "group:<QQ>"),
        (lambda source: source.update(scene="group:80001"), "scene"),
        (lambda source: source.update(persona="personas/group"), "persona"),
        (lambda source: source.update(panel=None), "panel"),
        (lambda source: source["scenes"]["group:80001"].update(database="other.db"), "database"),
        (lambda source: source["scenes"]["group:80001"].pop("persona"), "scenes.group:80001.persona"),
        (lambda source: source["models"]["roles"]["mind"].update(provider="missing"), "models.roles.mind.provider"),
        (lambda source: source["scenes"]["private:80002"]["schedules"].update(owner="90001"), "bot_qq"),
    ],
)
def test_multiscene_host_rejects_invalid_or_unowned_settings(tmp_path, change, field):
    root = tmp_path / "lab"
    source = _host_config()
    change(source)
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_host_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize("limit", [0, -1, True, 2.5, "4"])
def test_multiscene_host_rejects_invalid_model_slots(tmp_path, limit):
    root = tmp_path / "lab"
    source = _host_config()
    source["max_model_requests"] = limit
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_host_config(root)
    assert "max_model_requests" in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


def test_multiscene_host_rejects_database_outside_instance_directory(tmp_path):
    root = tmp_path / "lab"
    source = _host_config()
    source["database"] = "../another-chat.sqlite3"
    _write_config(root, source)

    with pytest.raises(ValueError, match="database must resolve inside the lab root"):
        load_host_config(root)


def test_multiscene_host_accepts_explicit_positive_model_slots(tmp_path):
    root = tmp_path / "lab"
    source = _host_config()
    source["max_model_requests"] = 2
    _write_config(root, source)

    host = load_host_config(root)

    assert host.max_model_requests == 2
    assert HostConfig.model_validate_json(host.model_dump_json()) == host


def test_history_import_paths_and_explicit_scene_scope_roundtrip(tmp_path):
    single_root = tmp_path / "single"
    single_source = _config("personas/example")
    single_source["history_import"] = {
        "source": "../offline-old.sqlite3", "backup": "data/pre-import.sqlite3",
        "scenes": ["group:80001"],
    }
    _write_config(single_root, single_source)

    single = load_instance_config(single_root)

    assert isinstance(single, LabConfig)
    assert single == load_config(single_root)
    assert isinstance(single.history_import, HistoryImportSettings)
    assert single.history_import.source == tmp_path / "offline-old.sqlite3"
    assert single.history_import.backup == single_root / "data/pre-import.sqlite3"
    assert single.history_import.scenes == [single.scene]
    assert single.history_import.recent_messages == 50
    assert LabConfig.model_validate_json(single.model_dump_json()) == single

    host_root = tmp_path / "multi"
    host_source = _host_config()
    host_source["history_import"] = {
        "source": "../offline-old.sqlite3", "backup": "data/pre-import.sqlite3",
        "scenes": ["private:80002"], "recent_messages": 17,
    }
    _write_config(host_root, host_source)

    host = load_instance_config(host_root)

    assert isinstance(host, HostConfig)
    assert host == load_host_config(host_root)
    assert host.history_import.source == tmp_path / "offline-old.sqlite3"
    assert host.history_import.backup == host_root / "data/pre-import.sqlite3"
    assert host.history_import.scenes == ["private:80002"]
    assert host.history_import.recent_messages == 17
    assert HostConfig.model_validate_json(host.model_dump_json()) == host
    assert host.scene_config("private:80002").history_import is None
    assert host.scene_config("group:80001").history_import is None
    assert host.history_import.scenes == ["private:80002"]


def test_history_export_paths_and_scope_roundtrip_without_running_either_offline_action(tmp_path):
    single_root = tmp_path / "single"
    single_source = _config("personas/example")
    single_source["history_export"] = {
        "target": "data/legacy.sqlite3", "backup": "data/legacy-before-export.sqlite3",
        "scenes": ["group:80001"],
    }
    _write_config(single_root, single_source)

    single = load_instance_config(single_root)

    assert isinstance(single, LabConfig)
    assert isinstance(single.history_export, HistoryExportSettings)
    assert single.history_export.target == single_root / "data/legacy.sqlite3"
    assert single.history_export.backup == single_root / "data/legacy-before-export.sqlite3"
    assert single.history_export.scenes == [single.scene]
    assert single.history_import is None
    assert LabConfig.model_validate_json(single.model_dump_json()) == single
    assert not single.history_export.target.exists()
    assert not single.history_export.backup.exists()

    host_root = tmp_path / "multi"
    host_source = _host_config()
    host_source["history_import"] = {
        "source": "../offline-old.sqlite3", "backup": "data/pre-import.sqlite3",
        "scenes": ["private:80002"],
    }
    host_source["history_export"] = {
        "target": "data/legacy.sqlite3", "backup": "data/legacy-before-export.sqlite3",
        "scenes": ["group:80001"],
    }
    _write_config(host_root, host_source)

    host = load_instance_config(host_root)

    assert isinstance(host, HostConfig)
    assert host.history_import.scenes == ["private:80002"]
    assert host.history_export.target == host_root / "data/legacy.sqlite3"
    assert host.history_export.backup == host_root / "data/legacy-before-export.sqlite3"
    assert host.history_export.scenes == ["group:80001"]
    assert HostConfig.model_validate_json(host.model_dump_json()) == host
    for scene in host.scenes:
        derived = host.scene_config(scene)
        assert derived.history_import is None and derived.history_export is None
        assert LabConfig.model_validate_json(derived.model_dump_json()) == derived
    assert host.history_import.scenes == ["private:80002"]
    assert host.history_export.scenes == ["group:80001"]
    assert not host.history_export.target.exists()
    assert not host.history_export.backup.exists()


@pytest.mark.parametrize(
    ("mode", "scenes", "field"),
    [
        ("isolated", [], "scenes"),
        ("isolated", ["group:80001", "group:80001"], "repeat"),
        ("isolated", ["private:0"], "group:<QQ>"),
        ("isolated", ["private:80002"], "only the configured scene"),
        ("isolated-multi", ["group:80001", "group:80001"], "repeat"),
        ("isolated-multi", ["group:99999"], "not configured"),
    ],
)
def test_history_export_rejects_invalid_scene_selection(tmp_path, mode, scenes, field):
    root = tmp_path / "lab"
    source = _config("personas/example") if mode == "isolated" else _host_config()
    source["history_export"] = {
        "target": "data/legacy.sqlite3", "backup": "data/legacy-before-export.sqlite3",
        "scenes": scenes,
    }
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_instance_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda item: item.pop("target"), "history_export.target"),
        (lambda item: item.pop("backup"), "history_export.backup"),
        (lambda item: item.update(target="../outside.sqlite3"), "history_export.target"),
        (lambda item: item.update(backup="../outside.sqlite3"), "history_export.backup"),
        (lambda item: item.update(target="data/isolated-chat.db"), "database must differ"),
        (lambda item: item.update(backup="data/isolated-chat.db"), "database must differ"),
        (lambda item: item.update(backup="data/legacy.sqlite3"), "database must differ"),
        (lambda item: item.update(backup="data/../data/legacy.sqlite3"), "database must differ"),
        (lambda item: item.update(unexpected=True), "unexpected"),
    ],
)
def test_history_export_rejects_invalid_paths_and_extra_fields(tmp_path, change, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["history_export"] = {
        "target": "data/legacy.sqlite3", "backup": "data/legacy-before-export.sqlite3",
        "scenes": ["group:80001"],
    }
    change(source["history_export"])
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize(
    ("mode", "scenes", "field"),
    [
        ("isolated", [], "scenes"),
        ("isolated", ["group:80001", "group:80001"], "repeat"),
        ("isolated", ["group:0"], "group:<QQ>"),
        ("isolated", ["private:80002"], "only the configured scene"),
        ("isolated-multi", ["group:80001", "group:80001"], "repeat"),
        ("isolated-multi", ["group:99999"], "not configured"),
    ],
)
def test_history_import_rejects_invalid_scene_selection(tmp_path, mode, scenes, field):
    root = tmp_path / "lab"
    source = _config("personas/example") if mode == "isolated" else _host_config()
    source["history_import"] = {
        "source": "../offline-old.sqlite3", "backup": "data/pre-import.sqlite3",
        "scenes": scenes,
    }
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_instance_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize(
    ("change", "field"),
    [
        (lambda item: item.pop("source"), "history_import.source"),
        (lambda item: item.pop("backup"), "history_import.backup"),
        (lambda item: item.update(backup="../outside.sqlite3"), "history_import.backup"),
        (lambda item: item.update(source="data/isolated-chat.db"), "database must differ"),
        (lambda item: item.update(backup="data/isolated-chat.db"), "database must differ"),
        (lambda item: item.update(source="data/pre-import.sqlite3"), "database must differ"),
        (lambda item: item.update(recent_messages=0), "recent_messages"),
        (lambda item: item.update(recent_messages=-1), "recent_messages"),
        (lambda item: item.update(recent_messages=True), "recent_messages"),
        (lambda item: item.update(recent_messages="50"), "recent_messages"),
        (lambda item: item.update(recent_messages=1.5), "recent_messages"),
        (lambda item: item.update(unexpected=True), "unexpected"),
    ],
)
def test_history_import_rejects_invalid_paths_limits_and_fields(tmp_path, change, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["history_import"] = {
        "source": "../offline-old.sqlite3", "backup": "data/pre-import.sqlite3",
        "scenes": ["group:80001"],
    }
    change(source["history_import"])
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize("mode", [None, "other", 123])
def test_instance_config_rejects_missing_or_unknown_mode(tmp_path, mode):
    root = tmp_path / "lab"
    source = _config("personas/example")
    if mode is None:
        source.pop("mode")
    else:
        source["mode"] = mode
    _write_config(root, source)

    with pytest.raises(ValueError, match="mode must explicitly be isolated or isolated-multi"):
        load_instance_config(root)


def test_offline_version_upgrade_cli_selects_explicit_multiscene_root(tmp_path):
    root = tmp_path / "lab"
    source = _host_config()
    source["database"] = "isolated.sqlite3"
    _write_config(root, source)
    fixture = Path(__file__).parent / "fixtures/next/migration/v9-synthetic.sqlite3"
    shutil.copyfile(fixture, root / "isolated.sqlite3")

    completed = subprocess.run(
        [sys.executable, "-m", "len_bot.next.migrate"], cwd=root,
        text=True, capture_output=True, check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Offline migration completed" in completed.stdout
    with sqlite3.connect(root / "isolated.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
    with sqlite3.connect(root / "isolated.sqlite3.v9.bak") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9


@pytest.mark.parametrize("port", [0, 65535])
def test_panel_configuration_uses_existing_hash_and_resolves_external_assets(tmp_path, port):
    root = tmp_path / "lab"
    password_hash = hash_password("synthetic-panel-password", salt="synthetic-salt")
    source = _config("personas/example")
    source["panel"] = {
        "host": "127.0.0.1", "port": port, "username": "operator",
        "password_hash": password_hash, "cookie_secure": True,
        "assets_dir": "../isolated-assets",
    }
    _write_config(root, source)

    config = load_config(root)

    assert isinstance(config.panel, PanelSettings)
    assert config.panel.host == "127.0.0.1" and config.panel.port == port
    assert config.panel.username == "operator" and config.panel.password_hash == password_hash
    assert config.panel.cookie_secure is True
    assert config.panel.assets_dir == tmp_path / "isolated-assets"
    assert config.onebot is None and config.delivery == "simulated"
    assert config.model_settings("mind").reasoning_effort == "high"
    assert config.model_settings("mind").model == "sample-mind"
    assert LabConfig.model_validate_json(config.model_dump_json()) == config
    assert password_hash not in repr(config.panel)
    assert password_hash not in repr(config)


def test_panel_configuration_defaults_and_absolute_asset_path(tmp_path):
    root = tmp_path / "lab"
    password_hash = hash_password("synthetic-panel-password", salt="synthetic-salt")
    source = _config("personas/example")
    source["panel"] = {"host": "::1", "port": 8090, "username": "operator",
                       "password_hash": password_hash}
    _write_config(root, source)

    panel = load_config(root).panel

    assert panel.cookie_secure is False and panel.assets_dir is None
    assert PanelSettings.model_validate_json(panel.model_dump_json()) == panel

    source["panel"]["assets_dir"] = str(tmp_path / "built-assets")
    (root / "lenbot.config.json").write_text(json.dumps(source), encoding="utf-8")
    assert load_config(root).panel.assets_dir == tmp_path / "built-assets"


@pytest.mark.parametrize(
    ("panel", "field"),
    [
        ({"port": 0, "username": "operator"}, "host"),
        ({"host": "127.0.0.1", "username": "operator"}, "port"),
        ({"host": "127.0.0.1", "port": 0}, "username"),
        ({"host": "127.0.0.1", "port": 0, "username": "operator"}, "password_hash"),
        ({"host": "  "}, "host"),
        ({"username": "  "}, "username"),
        ({"host": 123}, "host"),
        ({"username": 123}, "username"),
        ({"port": -1}, "port"),
        ({"port": 65536}, "port"),
        ({"port": True}, "port"),
        ({"port": 0.0}, "port"),
        ({"port": "0"}, "port"),
        ({"cookie_secure": "false"}, "cookie_secure"),
        ({"assets_dir": ""}, "panel.assets_dir"),
        ({"assets_dir": 123}, "panel.assets_dir"),
        ({"unexpected": True}, "unexpected"),
        ("not-an-object", "panel"),
    ],
)
def test_panel_configuration_rejects_missing_or_invalid_fields(tmp_path, panel, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["panel"] = {"host": "127.0.0.1", "port": 0, "username": "operator",
                       "password_hash": hash_password("synthetic-panel-password", salt="synthetic-salt")}
    if isinstance(panel, dict):
        source["panel"].update(panel)
        for required in ("host", "port", "username", "password_hash"):
            if required not in panel and field == required:
                source["panel"].pop(required)
    else:
        source["panel"] = panel
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-panel-password" not in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize("invalid_hash", [
    "synthetic-panel-secret", "", "$" + "a" * 64,
    "salt$" + "a" * 63, "salt$" + "a" * 65,
    "salt$" + "G" * 64, "salt$" + "A" * 64, "salt$" + "a" * 64 + "$extra",
])
def test_panel_password_hash_format_error_does_not_echo_secret(tmp_path, invalid_hash):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["panel"] = {"host": "127.0.0.1", "port": 0, "username": "operator",
                       "password_hash": invalid_hash}
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert "panel.password_hash" in str(failure.value)
    if invalid_hash:
        assert invalid_hash not in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize(
    "settings",
    [
        {"max_chars": 25, "min_interval_seconds": 0.1,
         "max_interval_seconds": 1.5, "chars_per_second": 75.0},
        {"max_chars": 1, "min_interval_seconds": 0.0,
         "max_interval_seconds": 0.0, "chars_per_second": 1.0},
    ],
)
def test_text_delivery_configuration_preserves_explicit_values_and_roundtrips(tmp_path, settings):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["text_delivery"] = settings
    _write_config(root, source)

    config = load_config(root)

    for field, value in settings.items():
        assert getattr(config.text_delivery, field) == value
    assert LabConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize(
    ("settings", "field"),
    [
        ({"max_chars": 0}, "max_chars"),
        ({"max_chars": -1}, "max_chars"),
        ({"max_chars": 300.0}, "max_chars"),
        ({"max_chars": True}, "max_chars"),
        ({"min_interval_seconds": -0.1}, "min_interval_seconds"),
        ({"min_interval_seconds": True}, "min_interval_seconds"),
        ({"min_interval_seconds": float("nan")}, "min_interval_seconds"),
        ({"max_interval_seconds": -0.1}, "max_interval_seconds"),
        ({"max_interval_seconds": float("inf")}, "max_interval_seconds"),
        ({"max_interval_seconds": False}, "max_interval_seconds"),
        ({"chars_per_second": 0}, "chars_per_second"),
        ({"chars_per_second": -1}, "chars_per_second"),
        ({"chars_per_second": float("nan")}, "chars_per_second"),
        ({"chars_per_second": float("inf")}, "chars_per_second"),
        ({"chars_per_second": True}, "chars_per_second"),
        ({"min_interval_seconds": 2.1, "max_interval_seconds": 2.0},
         "min_interval_seconds must not exceed max_interval_seconds"),
        ({"unknown": True}, "unknown"),
    ],
)
def test_text_delivery_configuration_rejects_invalid_values(tmp_path, settings, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["text_delivery"] = settings
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize("value", [None, {}, {"timeout_seconds": 0.5}])
def test_web_read_configuration_is_optional_and_roundtrips(tmp_path, value):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["web_read"] = value
    _write_config(root, source)

    config = load_config(root)

    if value is None:
        assert config.web_read is None
    else:
        assert config.web_read is not None
        assert config.web_read.timeout_seconds == value.get("timeout_seconds", 20)
    assert config.model_settings("mind").model == "sample-mind"
    assert config.model_settings("mind").reasoning_effort == "high"
    assert config.model_settings("voice").model == "sample-voice"
    assert LabConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize(
    ("value", "field"),
    [
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": -1}, "timeout_seconds"),
        ({"timeout_seconds": float("nan")}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
        ({"timeout_seconds": "20"}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"unexpected": True}, "unexpected"),
    ],
)
def test_web_read_configuration_rejects_invalid_values(tmp_path, value, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["web_read"] = value
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


def test_explicit_vision_binding_and_image_limits_roundtrip_without_changing_other_roles(tmp_path):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["models"]["providers"]["image"] = {
        "api": "openai-chat", "base_url": "http://127.0.0.1:18081/v1",
        "api_key": "synthetic-image-secret",
    }
    source["models"]["roles"]["vision"] = {
        "provider": "image", "model": "sample-vision", "context_window_tokens": 4096,
        "temperature": 0.3, "max_output_tokens": 512, "timeout_seconds": 15.0,
        "reasoning_effort": "low",
    }
    source["images"] = {
        "max_bytes": 2048, "max_pixels": 4096,
        "max_dimension": 128, "timeout_seconds": 2.5,
    }
    _write_config(root, source)

    config = load_config(root)

    assert config.images.model_dump() == source["images"]
    vision = config.model_settings("vision")
    assert vision.api == "openai-chat"
    assert vision.base_url == "http://127.0.0.1:18081/v1"
    assert vision.model == "sample-vision"
    assert vision.temperature == 0.3 and vision.max_output_tokens == 512
    assert vision.timeout_seconds == 15.0 and vision.reasoning_effort == "low"
    assert config.model_settings("mind").model == "sample-mind"
    assert config.model_settings("mind").reasoning_effort == "high"
    assert config.model_settings("voice").model == "sample-voice"
    assert LabConfig.model_validate_json(config.model_dump_json()) == config
    assert "synthetic-image-secret" not in repr(config)


def test_vision_binding_rejects_unknown_provider(tmp_path):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["models"]["roles"]["vision"] = {
        "provider": "missing", "model": "sample-vision", "context_window_tokens": 4096,
    }
    _write_config(root, source)

    with pytest.raises(ValueError, match="models.roles.vision.provider references unknown provider") as failure:
        load_config(root)
    assert "synthetic-secret-marker" not in str(failure.value)


@pytest.mark.parametrize(
    ("images", "field"),
    [
        ({"max_bytes": 0}, "max_bytes"),
        ({"max_bytes": -1}, "max_bytes"),
        ({"max_bytes": 1.5}, "max_bytes"),
        ({"max_bytes": True}, "max_bytes"),
        ({"max_pixels": 0}, "max_pixels"),
        ({"max_pixels": -1}, "max_pixels"),
        ({"max_pixels": False}, "max_pixels"),
        ({"max_dimension": 0}, "max_dimension"),
        ({"max_dimension": -1}, "max_dimension"),
        ({"max_dimension": "1280"}, "max_dimension"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": -1}, "timeout_seconds"),
        ({"timeout_seconds": float("nan")}, "timeout_seconds"),
        ({"timeout_seconds": float("inf")}, "timeout_seconds"),
        ({"timeout_seconds": "20"}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"unknown": 1}, "unknown"),
    ],
)
def test_image_limits_reject_invalid_values(tmp_path, images, field):
    root = tmp_path / "lab"
    source = _config("personas/example")
    source["images"] = images
    _write_config(root, source)

    with pytest.raises(ValueError) as failure:
        load_config(root)
    assert field in str(failure.value)
    assert "synthetic-secret-marker" not in str(failure.value)


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
    assert persona.knowledge == {}
    assert "knowledge" not in persona.model_dump()


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


def _synthetic_persona_package(path: Path) -> None:
    path.mkdir()
    (path / "persona.yaml").write_text(
        "id: example\nname: 示例角色\nbrief: 示例\nbehavior: 示例\n"
        "self_reference: [我]\naliases: [小例]\ntools: all\nskills: []\nstyles: []\n",
        encoding="utf-8",
    )
    (path / "voice.md").write_text("短句", encoding="utf-8")
    (path / "boundaries.md").write_text("示例", encoding="utf-8")
    (path / "examples.yaml").write_text("[]\n", encoding="utf-8")


def test_persona_knowledge_loads_nested_original_text_as_private_snapshot(tmp_path):
    path = tmp_path / "example"
    _synthetic_persona_package(path)
    document = path / "knowledge" / "world" / "story.md"
    document.parent.mkdir(parents=True)
    original = (
        "---\r\ntags: [舞台, 二期]\r\ndate: 2020-01-02\r\n"
        "confidence: 传闻，待核实\r\n---\r\n# 世界观\r\n原文与日期保持不变。\r\n"
    )
    document.write_bytes(original.encode("utf-8"))

    persona = load_persona(path)

    assert list(persona.knowledge) == ["world/story.md"]
    assert persona.knowledge["world/story.md"].tags == ("舞台", "二期")
    assert persona.knowledge["world/story.md"].content == original
    assert "knowledge" not in persona.model_dump()
    assert "knowledge" not in repr(persona)
    document.write_text("运行后磁盘上的新内容", encoding="utf-8")
    assert persona.knowledge["world/story.md"].content == original


@pytest.mark.parametrize("header", ["tags: 舞台", "tags: [舞台, 3]", "tags: null"])
def test_persona_knowledge_rejects_non_string_list_tags(tmp_path, header):
    path = tmp_path / "example"
    _synthetic_persona_package(path)
    document = path / "knowledge" / "invalid.md"
    document.parent.mkdir()
    document.write_text(f"---\n{header}\n---\n正文\n", encoding="utf-8")

    with pytest.raises(ValueError) as failure:
        load_persona(path)
    assert "invalid.md" in str(failure.value)
    assert "tags" in str(failure.value)


@pytest.mark.parametrize("source,fragment", [
    (b"---\ntags: [broken\n---\nbody\n", "tags: [broken"),
    (b"---\ntags: [ok]\nbody without ending line\n", "tags: [ok]"),
    (b"\xff\n", "ff"),
])
def test_persona_knowledge_reports_invalid_source_and_fragment(tmp_path, source, fragment):
    path = tmp_path / "example"
    _synthetic_persona_package(path)
    document = path / "knowledge" / "invalid.md"
    document.parent.mkdir()
    document.write_bytes(source)

    with pytest.raises((ValueError, UnicodeError)) as failure:
        load_persona(path)
    assert "invalid.md" in str(failure.value)
    assert fragment in str(failure.value)


def test_persona_yaml_cannot_inject_knowledge_snapshot(tmp_path):
    path = tmp_path / "example"
    _synthetic_persona_package(path)
    with (path / "persona.yaml").open("a", encoding="utf-8") as output:
        output.write("knowledge: {forged.md: {content: 假资料, tags: []}}\n")

    with pytest.raises(ValueError, match="knowledge"):
        load_persona(path)
