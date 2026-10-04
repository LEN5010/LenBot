"""Authentication and scene boundaries of the isolated web test surface."""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from len_bot.next.configuration.onebot import OneBotForward
from len_bot.next.config import load_config
from len_bot.next.trials.panel import create_app
from len_bot.next.storage.store import Store
from len_bot.web.auth import create_session, hash_password, revoke_session


@pytest.fixture
def panel_root(tmp_path):
    root = tmp_path / "isolated"
    root.mkdir()
    return root


@pytest.fixture
def panel_config(panel_root):
    root = panel_root
    persona = root / "persona"
    persona.mkdir(parents=True)
    (persona / "persona.yaml").write_text(
        "id: permission-test\nname: 测试角色\nbrief: 仅用于权限测试。\n"
        "behavior: 简短回答。\nself_reference: [我]\naliases: []\n"
        "tools: []\nskills: []\nstyles: []\n",
        encoding="utf-8",
    )
    (persona / "voice.md").write_text("简短。", encoding="utf-8")
    (persona / "boundaries.md").write_text("仅限测试。", encoding="utf-8")
    (persona / "examples.yaml").write_text("[]\n", encoding="utf-8")
    config = {"compaction": {"input_tokens": 2000},
        "mode": "isolated",
        "scene": "group:80001",
        "bot_qq": "90001",
        "timezone": "Asia/Shanghai",
        "database": "data/panel.sqlite3",
        "persona": "persona",
        "panel": {
            "host": "127.0.0.1", "port": 0,
            "username": "isolated-operator",
            "password_hash": hash_password("synthetic-panel-password", salt="synthetic-panel-salt"),
        },
        "models": {
            "providers": {"local": {
                "api": "openai-chat", "base_url": "http://127.0.0.1:9/v1",
                "api_key": "synthetic-unused-key",
            }},
            "roles": {
                "mind": {"provider": "local", "model": "synthetic-mind",
                         "context_window_tokens": 8192},

            },
        },
    }
    (root / "lenbot.config.json").write_text(json.dumps(config), encoding="utf-8")
    return load_config(root)


def _login(client: TestClient):
    return client.post("/api/auth/login", json={
        "username": "isolated-operator", "password": "synthetic-panel-password",
    })


def test_unauthed_http_and_websocket_are_rejected(panel_config, panel_root):
    with TestClient(create_app(panel_config, root=panel_root)) as client:
        assert client.get("/api/panel-context").json() == {
            "mode": "isolated", "home": "/chat-test",
        }
        for path in ("/api/auth/me", "/api/chat-test/state", "/api/chat-test/settings",
                     "/api/chat-test/scene-persona",
                     "/api/chat-test/turns/no-turn"):
            assert client.get(path).status_code == 401
        assert client.put("/api/chat-test/scene-persona", json={
            "persona_aliases": [], "relationships": {}, "behavior_addendum": None,
        }).status_code == 401
        assert client.post("/api/chat-test/messages", json={
            "uid": "80002", "nickname": "测试者", "text": "不会入库", "mention_bot": True,
            "reply_to": None,
        }).status_code == 401
        assert client.post("/api/auth/logout").status_code == 401
        with pytest.raises(WebSocketDisconnect) as failure:
            with client.websocket_connect("/api/chat-test/events"):
                pass
        assert failure.value.code == 1008

    with Store(panel_config.database) as store:
        assert store.recent_records(panel_config.scene) == []


def test_login_validation_names_missing_username_without_echoing_password(panel_config, panel_root):
    password = "synthetic-panel-secret-marker"
    with TestClient(create_app(panel_config, root=panel_root)) as client:
        response = client.post("/api/auth/login", json={"password": password})
        assert response.status_code == 422
        assert password not in response.text
        assert any(item["loc"] == ["body", "username"]
                   and item["type"] == "missing" and item["msg"]
                   for item in response.json()["detail"])


def test_virtual_sender_cannot_use_configured_bot_identity(panel_config, panel_root):
    with TestClient(create_app(panel_config, root=panel_root)) as client:
        assert _login(client).status_code == 200
        response = client.post("/api/chat-test/messages", json={
            "uid": panel_config.bot_qq, "nickname": "伪装自身", "text": "不能保存的虚拟消息",
            "mention_bot": False, "reply_to": None,
        })
        assert response.status_code == 422
        assert "Bot" in response.json()["detail"]
        assert client.get("/api/chat-test/state").json()["messages"] == []
        assert client.post("/api/auth/logout").status_code == 200
    with Store(panel_config.database) as store:
        assert store.recent_records(panel_config.scene) == []


def test_configured_account_uses_separate_cookie_name_and_logout_revokes_it(panel_config, panel_root):
    legacy_token = create_session("legacy-operator")
    old_test_token = create_session("previous-test-operator")
    other_port_token = create_session("other-port-operator")
    try:
        with TestClient(create_app(panel_config, root=panel_root)) as client:
            client.cookies.set("session_token", legacy_token)
            client.cookies.set("lenbot_test_session", old_test_token)
            client.cookies.set("lenbot_test_session_p81", other_port_token)
            assert client.get("/api/auth/me").status_code == 401
            assert client.get("/api/chat-test/state").status_code == 401
            assert client.get("/api/chat-test/settings").status_code == 401
            assert client.get("/api/chat-test/scene-persona").status_code == 401
            assert client.put("/api/chat-test/scene-persona", json={
                "persona_aliases": [], "relationships": {}, "behavior_addendum": None,
            }).status_code == 401
            with pytest.raises(WebSocketDisconnect) as failure:
                with client.websocket_connect("/api/chat-test/events"):
                    pass
            assert failure.value.code == 1008

            assert client.post("/api/auth/login", json={
                "username": "admin", "password": "synthetic-panel-password",
            }).status_code == 401
            assert client.post("/api/auth/login", json={
                "username": "isolated-operator", "password": "wrong-password",
            }).status_code == 401

            login = _login(client)
            assert login.status_code == 200
            assert login.json()["username"] == "isolated-operator"
            assert "lenbot_test_session_p80" in login.cookies
            assert "session_token" not in login.cookies
            token = login.cookies["lenbot_test_session_p80"]
            assert client.get("/api/auth/me").json()["username"] == "isolated-operator"
            state = client.get("/api/chat-test/state")
            assert state.status_code == 200
            assert state.json()["scene"] == panel_config.scene
            assert state.json()["delivery"] == "simulated"
            assert "synthetic-unused-key" not in state.text
            assert panel_config.panel.password_hash not in state.text
            settings = client.get("/api/chat-test/settings")
            assert settings.status_code == 200
            assert "synthetic-unused-key" not in settings.text
            assert panel_config.panel.password_hash not in settings.text
            assert "synthetic-panel-password" not in settings.text
            saved = client.get("/api/chat-test/scene-persona")
            assert saved.status_code == 200
            assert saved.json() == {
                "saved": {"persona_aliases": [], "relationships": {}, "behavior_addendum": None},
                "restart_required": False,
            }

            assert client.post("/api/auth/logout").status_code == 200
            assert client.cookies["session_token"] == legacy_token
            assert client.cookies["lenbot_test_session"] == old_test_token
            assert client.cookies["lenbot_test_session_p81"] == other_port_token
            assert client.get("/api/auth/me").status_code == 401
            client.cookies.set("lenbot_test_session_p80", token)
            assert client.get("/api/chat-test/state").status_code == 401
            assert client.get("/api/chat-test/settings").status_code == 401
            assert client.get("/api/chat-test/scene-persona").status_code == 401
            assert client.put("/api/chat-test/scene-persona", json={
                "persona_aliases": [], "relationships": {}, "behavior_addendum": None,
            }).status_code == 401
    finally:
        revoke_session(legacy_token)
        revoke_session(old_test_token)
        revoke_session(other_port_token)


@pytest.mark.parametrize("base_url,cookie", [
    ("http://testserver", "lenbot_test_session_p80"),
    ("https://testserver", "lenbot_test_session_p443"),
    ("http://testserver:56789", "lenbot_test_session_p56789"),
])
def test_cookie_name_follows_effective_request_port_not_configured_listener(
        panel_config, panel_root, base_url, cookie):
    assert panel_config.panel.port == 0
    with TestClient(create_app(panel_config, root=panel_root), base_url=base_url) as client:
        login = _login(client)
        assert login.status_code == 200
        assert cookie in login.cookies
        assert "lenbot_test_session_p0" not in login.cookies
        assert client.get("/api/auth/me").status_code == 200
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401


def test_http_and_websocket_use_same_effective_port_cookie(panel_config, panel_root):
    with TestClient(create_app(panel_config, root=panel_root),
                    base_url="http://testserver:56789") as client:
        login = _login(client)
        assert login.status_code == 200
        assert "lenbot_test_session_p56789" in login.cookies
        with client.websocket_connect("ws://testserver:56789/api/chat-test/events") as websocket:
            assert websocket.receive_json() == {"type": "changed"}
            assert client.post("/api/auth/logout").status_code == 200
            with pytest.raises(WebSocketDisconnect) as failure:
                websocket.receive_json()
            assert failure.value.code == 1008


def test_https_and_wss_share_implicit_443_cookie(panel_config, panel_root):
    with TestClient(create_app(panel_config, root=panel_root), base_url="https://testserver") as client:
        login = _login(client)
        assert login.status_code == 200
        assert "lenbot_test_session_p443" in login.cookies
        with client.websocket_connect("wss://testserver/api/chat-test/events") as websocket:
            assert websocket.receive_json() == {"type": "changed"}
            assert client.post("/api/auth/logout").status_code == 200
            with pytest.raises(WebSocketDisconnect) as failure:
                websocket.receive_json()
            assert failure.value.code == 1008


def test_logout_closes_an_existing_authenticated_websocket(panel_config, panel_root):
    with TestClient(create_app(panel_config, root=panel_root)) as client:
        assert _login(client).status_code == 200
        with client.websocket_connect("/api/chat-test/events") as websocket:
            assert websocket.receive_json() == {"type": "changed"}
            assert client.post("/api/auth/logout").status_code == 200
            with pytest.raises(WebSocketDisconnect) as failure:
                websocket.receive_json()
            assert failure.value.code == 1008
        assert client.get("/api/chat-test/state").status_code == 401


def test_turn_lookup_is_scoped_to_configured_scene(panel_config, panel_root):
    with Store(panel_config.database) as store:
        local_turn = store.start_turn(panel_config.scene)
        request = {"settings": panel_config.model_settings("mind").model_dump(exclude={"api_key"}),
                   "messages": [{"role": "system", "content": "明确合成的既有会话"}], "tools": []}
        response = {"message": {"role": "assistant", "content": None, "tool_calls": [
            {"id": "shared-provider-id", "type": "function",
             "function": {"name": "say", "arguments": '{"content":"合成表达"}'}}
        ]}, "finish_reason": "tool_calls"}
        local_call = store.start_call(local_turn, "mind", request)
        store.end_call(local_call, response, None, append_to_scene=panel_config.scene)
        store.complete_tool(panel_config.scene, "shared-provider-id", "本场景的合成结果")
        store.end_turn(local_turn, "settled")
        other_turn = store.start_turn("group:80002")
        other_call = store.start_call(other_turn, "mind", request)
        store.end_call(other_call, response, None, append_to_scene="group:80002")
        store.complete_tool("group:80002", "shared-provider-id", "其他场景不可见的合成结果")
        store.end_turn(other_turn, "settled")

    with TestClient(create_app(panel_config, root=panel_root)) as client:
        assert _login(client).status_code == 200
        local = client.get(f"/api/chat-test/turns/{local_turn}")
        assert local.status_code == 200
        assert local.json()["turn"]["scene"] == panel_config.scene
        assert "本场景的合成结果" in local.text
        assert "其他场景不可见的合成结果" not in local.text
        assert client.get(f"/api/chat-test/turns/{other_turn}").status_code == 404
        assert client.post("/api/auth/logout").status_code == 200


def test_web_panel_rejects_nonisolated_outlet_configurations(panel_config, panel_root):
    with pytest.raises(ValueError, match="requires panel configuration"):
        create_app(panel_config.model_copy(update={"panel": None}), root=panel_root)
    transport = OneBotForward(mode="forward_ws", ws_url="ws://127.0.0.1:9")
    with pytest.raises(ValueError, match="onebot=null and delivery=simulated"):
        create_app(panel_config.model_copy(update={"onebot": transport}), root=panel_root)
    with pytest.raises(ValueError, match="onebot=null and delivery=simulated"):
        create_app(panel_config.model_copy(update={"onebot": transport, "delivery": "onebot"}), root=panel_root)


def test_scene_persona_rejects_scope_smuggling_without_any_file_or_runtime_change(panel_config, panel_root):
    def saved_files():
        return {path.relative_to(panel_root).as_posix(): path.read_bytes()
                for path in panel_root.rglob("*") if path.is_file()}

    with TestClient(create_app(panel_config, root=panel_root)) as client:
        assert _login(client).status_code == 200
        current = client.get("/api/chat-test/settings").json()["scene_persona"]
        saved = client.get("/api/chat-test/scene-persona").json()
        baseline = saved_files()
        proposed = {"persona_aliases": ["合成本群称呼"],
                    "relationships": {"70001": "合成关系"},
                    "behavior_addendum": "本群简短回答。"}
        for field, value in (("persona", "other-persona"), ("model", "other-model"),
                             ("models", {"mind": "other-model"}),
                             ("scene", "group:80002"), ("root", "/private/tmp/other")):
            response = client.put("/api/chat-test/scene-persona", json={**proposed, field: value})
            assert response.status_code == 422, (field, response.text)
            assert saved_files() == baseline, field
            assert client.get("/api/chat-test/scene-persona").json() == saved, field
            assert client.get("/api/chat-test/settings").json()["scene_persona"] == current, field
        assert client.post("/api/auth/logout").status_code == 200
