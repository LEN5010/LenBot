import pytest
from httpx import AsyncClient, ASGITransport
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.web.app import create_app
from len_bot.adapters.onebot import OneBotAdapter

@pytest.mark.asyncio
async def test_dashboard_auth_and_management(tmp_path, monkeypatch):
    db_file = str(tmp_path / "dashboard_test.db")
    config = RuntimeConfig(
        db_path=db_file,
        dashboard_enabled=False, # We test app directly via AsyncClient
        dashboard_port=11307,
        dashboard_default_admin_user="admin",
        dashboard_default_admin_password="lenbot123"
    )

    runtime = AgentRuntime(config=config)
    await runtime.start()
    adapter = OneBotAdapter(config, on_event=runtime.receive_event)
    runtime._onebot_adapter = adapter
    restarted = False

    async def fake_restart():
        nonlocal restarted
        restarted = True

    monkeypatch.setattr(adapter, "restart", fake_restart)

    app = create_app(runtime)
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Access protected route without auth -> 401
        res = await client.get("/api/overview/stats")
        assert res.status_code == 401

        # 2. Login with bad credentials -> 401
        bad_login = await client.post("/api/auth/login", json={"username": "admin", "password": "wrongpassword"})
        assert bad_login.status_code == 401

        # 3. Login with valid default credentials -> 200
        login_res = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert login_res.status_code == 200
        data = login_res.json()
        assert data["success"] is True
        assert data["username"] == "admin"
        assert data["is_default_password"] is True
        assert "session_token" in login_res.cookies
        headers = {}

        # 4. Check /api/auth/me
        me_res = await client.get("/api/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["username"] == "admin"

        # 5. Overview Bento stats
        stats_res = await client.get("/api/overview/stats", headers=headers)
        assert stats_res.status_code == 200
        stats_data = stats_res.json()
        assert "total_events" in stats_data["stats"]
        assert "social_cognition" in stats_data["social_metrics"]

        # 6. WebSocket status
        ws_res = await client.get("/api/websocket/status", headers=headers)
        assert ws_res.status_code == 200
        assert ws_res.json()["port"] == config.ws_port

        onebot_update = await client.post("/api/websocket/config", headers=headers, json={
            "connection_mode": "forward_ws",
            "action_transport": "http",
            "ws_url": "ws://127.0.0.1:13001/",
            "http_url": "http://127.0.0.1:13000/",
            "host": "127.0.0.1",
            "port": 8080,
            "access_token": "onebot-secret",
        })
        assert onebot_update.status_code == 200
        assert restarted is True
        onebot_status = (await client.get("/api/websocket/status", headers=headers)).json()
        assert onebot_status["connection_mode"] == "forward_ws"
        assert onebot_status["action_transport"] == "http"
        assert onebot_status["access_token_set"] is True
        assert "access_token" not in onebot_status

        unknown_api = await client.get("/api/this-route-does-not-exist", headers=headers)
        assert unknown_api.status_code == 404
        assert "重启 LenBot" in unknown_api.json()["detail"]

        # 7. Provider & Routing management (ADR-0020)
        providers_get = await client.get("/api/models/providers", headers=headers)
        assert providers_get.status_code == 200
        providers_data = providers_get.json()
        # Starting the control plane does not configure or select a model.
        assert providers_data == {"providers": [], "routing": None}

        provider_post = await client.post("/api/models/providers", headers=headers, json={
            "id": "openai-main",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test1234567890",
            "enabled": True
        })
        assert provider_post.status_code == 200

        async def fake_models(provider_id):
            assert provider_id == "openai-main"
            return ["gpt-4o", "gpt-4o-mini"]

        monkeypatch.setattr(runtime.provider_registry, "list_models", fake_models)
        models_get = await client.get("/api/models/providers/openai-main/models", headers=headers)
        assert models_get.status_code == 200
        assert models_get.json()["models"] == ["gpt-4o", "gpt-4o-mini"]
        models_save = await client.post(
            "/api/models/providers/openai-main/models",
            headers=headers,
            json={"models": ["gpt-4o-mini", "gpt-4o"]},
        )
        assert models_save.status_code == 200

        routing_post = await client.post("/api/models/routing", headers=headers, json={
            "conversation": {"provider_id": "openai-main", "model": "gpt-4o-mini"},
            "work": {"provider_id": "openai-main", "model": "gpt-4o", "reasoning_effort": "high"},
        })
        assert routing_post.status_code == 200

        # Hot-applied: registry resolves the new routing immediately
        normal_res = runtime.provider_registry.resolve("conversation")
        assert normal_res.model == "gpt-4o-mini"
        assert normal_res.provider_id == "openai-main"
        assert runtime.provider_registry.resolve("work").model == "gpt-4o"
        assert runtime.provider_registry.resolve("work").reasoning_effort == "high"

        # A model already used by routing remains selectable even if it is
        # accidentally unchecked in the provider catalog.
        catalog_update = await client.post(
            "/api/models/providers/openai-main/models",
            headers=headers,
            json={"models": ["gpt-4o"]},
        )
        assert catalog_update.status_code == 200
        assert catalog_update.json()["models"] == ["gpt-4o", "gpt-4o-mini"]
        assert "已保留" in catalog_update.json()["message"]

        # API key never echoed back
        providers_after = (await client.get("/api/models/providers", headers=headers)).json()
        for p in providers_after["providers"]:
            assert "api_key" not in p
            assert "api_key_masked" in p

        # Persisted: survives a fresh runtime on the same DB
        runtime2 = AgentRuntime(config=RuntimeConfig(db_path=db_file))
        await runtime2.start()
        normal2 = runtime2.provider_registry.resolve("conversation")
        assert normal2.model == "gpt-4o-mini" and normal2.provider_id == "openai-main"
        assert normal2.client.base_url.host == "api.openai.com"
        assert runtime2.provider_registry.resolve("work").model == "gpt-4o"
        assert runtime2.provider_registry.resolve("work").reasoning_effort == "high"
        assert runtime2.config.onebot_connection_mode == "forward_ws"
        assert runtime2.config.onebot_action_transport == "http"
        assert runtime2.config.onebot_ws_url == "ws://127.0.0.1:13001/"
        assert runtime2.config.onebot_access_token == "onebot-secret"
        await runtime2.stop()

        # Routing metrics endpoint exists
        metrics_res = await client.get("/api/models/metrics", headers=headers)
        assert metrics_res.status_code == 200
        assert "social" in metrics_res.json()

        # 8. Persona Settings
        persona_post = await client.post("/api/settings/persona", headers=headers, json={
            "identity_name": "LenAdmin",
            "identity_persona": "Custom test persona",
            "bot_qq": 987654321
        })
        assert persona_post.status_code == 200
        assert runtime.config.identity_name == "LenAdmin"
        assert runtime.config.bot_qq == 987654321

        # 9. Plugins Subsystem (real registry only, ADR-0021)
        plugins_get = await client.get("/api/plugins/list", headers=headers)
        assert plugins_get.status_code == 200
        plugins_list = plugins_get.json()
        assert {p["id"] for p in plugins_list} == {"bilibili_live_sensor", "web_search_tool", "bilibili_content"}
        first_plugin_id = plugins_list[0]["id"]

        plugin_toggle = await client.post("/api/plugins/toggle", headers=headers, json={
            "plugin_id": first_plugin_id,
            "enabled": True
        })
        assert plugin_toggle.status_code == 200
        assert plugin_toggle.json()["enabled"] is True

        # 10. Change Password & re-login
        change_pwd = await client.post("/api/auth/change_password", headers=headers, json={
            "current_password": "lenbot123",
            "new_password": "supersecretpassword456"
        })
        assert change_pwd.status_code == 200

        # Login with old password must fail
        old_login = await client.post("/api/auth/login", json={"username": "admin", "password": "lenbot123"})
        assert old_login.status_code == 401

        # Login with new password must succeed
        new_login = await client.post("/api/auth/login", json={"username": "admin", "password": "supersecretpassword456"})
        assert new_login.status_code == 200
        assert new_login.json()["is_default_password"] is False

    await runtime.stop()
