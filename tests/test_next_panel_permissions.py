"""Authentication and scene boundaries of the isolated web test surface."""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from len_bot.next.config import OneBotForward, load_config
from len_bot.next.panel import create_app
from len_bot.next.store import Store
from len_bot.web.auth import create_session, hash_password, revoke_session


@pytest.fixture
def panel_config(tmp_path):
    root = tmp_path / "isolated"
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
    config = {
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
                "voice": {"provider": "local", "model": "synthetic-voice",
                          "context_window_tokens": 4096},
            },
        },
    }
    (root / "lenbot.config.json").write_text(json.dumps(config), encoding="utf-8")
    return load_config(root)


def _login(client: TestClient):
    return client.post("/api/auth/login", json={
        "username": "isolated-operator", "password": "synthetic-panel-password",
    })


def test_unauthed_http_and_websocket_are_rejected(panel_config):
    with TestClient(create_app(panel_config)) as client:
        assert client.get("/api/panel-context").json() == {
            "mode": "isolated", "home": "/chat-test",
        }
        for path in ("/api/auth/me", "/api/chat-test/state", "/api/chat-test/turns/no-turn"):
            assert client.get(path).status_code == 401
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


def test_login_validation_names_missing_username_without_echoing_password(panel_config):
    password = "synthetic-panel-secret-marker"
    with TestClient(create_app(panel_config)) as client:
        response = client.post("/api/auth/login", json={"password": password})
        assert response.status_code == 422
        assert password not in response.text
        assert any(item["loc"] == ["body", "username"]
                   and item["type"] == "missing" and item["msg"]
                   for item in response.json()["detail"])


def test_virtual_sender_cannot_use_configured_bot_identity(panel_config):
    with TestClient(create_app(panel_config)) as client:
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


def test_configured_account_uses_separate_cookie_name_and_logout_revokes_it(panel_config):
    legacy_token = create_session("legacy-operator")
    try:
        with TestClient(create_app(panel_config)) as client:
            client.cookies.set("session_token", legacy_token)
            assert client.get("/api/auth/me").status_code == 401
            assert client.get("/api/chat-test/state").status_code == 401

            assert client.post("/api/auth/login", json={
                "username": "admin", "password": "synthetic-panel-password",
            }).status_code == 401
            assert client.post("/api/auth/login", json={
                "username": "isolated-operator", "password": "wrong-password",
            }).status_code == 401

            login = _login(client)
            assert login.status_code == 200
            assert login.json()["username"] == "isolated-operator"
            assert "lenbot_test_session" in login.cookies
            assert "session_token" not in login.cookies
            token = login.cookies["lenbot_test_session"]
            assert client.get("/api/auth/me").json()["username"] == "isolated-operator"
            state = client.get("/api/chat-test/state")
            assert state.status_code == 200
            assert state.json()["scene"] == panel_config.scene
            assert state.json()["delivery"] == "simulated"
            assert "synthetic-unused-key" not in state.text
            assert panel_config.panel.password_hash not in state.text

            assert client.post("/api/auth/logout").status_code == 200
            assert client.get("/api/auth/me").status_code == 401
            client.cookies.set("lenbot_test_session", token)
            assert client.get("/api/chat-test/state").status_code == 401
    finally:
        revoke_session(legacy_token)


def test_logout_closes_an_existing_authenticated_websocket(panel_config):
    with TestClient(create_app(panel_config)) as client:
        assert _login(client).status_code == 200
        with client.websocket_connect("/api/chat-test/events") as websocket:
            assert websocket.receive_json() == {"type": "changed"}
            assert client.post("/api/auth/logout").status_code == 200
            with pytest.raises(WebSocketDisconnect) as failure:
                websocket.receive_json()
            assert failure.value.code == 1008
        assert client.get("/api/chat-test/state").status_code == 401


def test_turn_lookup_is_scoped_to_configured_scene(panel_config):
    with Store(panel_config.database) as store:
        local_turn = store.start_turn(panel_config.scene)
        store.end_turn(local_turn, "settled")
        other_turn = store.start_turn("group:80002")
        store.end_turn(other_turn, "settled")

    with TestClient(create_app(panel_config)) as client:
        assert _login(client).status_code == 200
        local = client.get(f"/api/chat-test/turns/{local_turn}")
        assert local.status_code == 200
        assert local.json()["turn"]["scene"] == panel_config.scene
        assert client.get(f"/api/chat-test/turns/{other_turn}").status_code == 404
        assert client.post("/api/auth/logout").status_code == 200


def test_web_panel_rejects_nonisolated_outlet_configurations(panel_config):
    with pytest.raises(ValueError, match="requires panel configuration"):
        create_app(panel_config.model_copy(update={"panel": None}))
    transport = OneBotForward(mode="forward_ws", ws_url="ws://127.0.0.1:9")
    with pytest.raises(ValueError, match="onebot=null and delivery=simulated"):
        create_app(panel_config.model_copy(update={"onebot": transport}))
    with pytest.raises(ValueError, match="onebot=null and delivery=simulated"):
        create_app(panel_config.model_copy(update={"onebot": transport, "delivery": "onebot"}))
