import pytest
from httpx import AsyncClient, ASGITransport
from len_bot.config import RuntimeConfig
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.cognition.router import CognitiveTier
from len_bot.web.app import create_app

@pytest.mark.asyncio
async def test_dashboard_auth_and_management(tmp_path):
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
        token = data["token"]

        headers = {"Authorization": f"Bearer {token}"}

        # 4. Check /api/auth/me
        me_res = await client.get("/api/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["username"] == "admin"

        # 5. Overview Bento stats
        stats_res = await client.get("/api/overview/stats", headers=headers)
        assert stats_res.status_code == 200
        stats_data = stats_res.json()
        assert "total_events" in stats_data["stats"]
        assert "speaking_budget_threshold" in stats_data["stats"]

        # 6. WebSocket status
        ws_res = await client.get("/api/websocket/status", headers=headers)
        assert ws_res.status_code == 200
        assert ws_res.json()["port"] == config.ws_port

        # 7. Provider & Routing management (ADR-0020)
        providers_get = await client.get("/api/models/providers", headers=headers)
        assert providers_get.status_code == 200
        providers_data = providers_get.json()
        # Startup seeded the default provider from config (no legacy model_config existed)
        assert any(p["id"] == "default" for p in providers_data["providers"])
        assert providers_data["routing"]["normal"]["model"] == "deepseek-chat"

        provider_post = await client.post("/api/models/providers", headers=headers, json={
            "id": "openai-main",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test1234567890",
            "enabled": True
        })
        assert provider_post.status_code == 200

        routing_post = await client.post("/api/models/routing", headers=headers, json={
            "normal_provider_id": "openai-main",
            "normal_model": "gpt-4o-mini",
            "deliberate_provider_id": "openai-main",
            "deliberate_model": "gpt-4o"
        })
        assert routing_post.status_code == 200

        # Hot-applied: registry resolves the new routing immediately
        normal_res = runtime.provider_registry.resolve(CognitiveTier.NORMAL)
        assert normal_res.model == "gpt-4o-mini"
        assert normal_res.provider_id == "openai-main"

        # API key never echoed back
        providers_after = (await client.get("/api/models/providers", headers=headers)).json()
        for p in providers_after["providers"]:
            assert "api_key" not in p
            assert "api_key_masked" in p

        # Persisted: survives a fresh runtime on the same DB
        runtime2 = AgentRuntime(config=config)
        await runtime2.start()
        normal2 = runtime2.provider_registry.resolve(CognitiveTier.NORMAL)
        assert normal2.model == "gpt-4o-mini" and normal2.provider_id == "openai-main"
        assert normal2.client.base_url.host == "api.openai.com"
        await runtime2.stop()

        # Routing metrics endpoint exists
        metrics_res = await client.get("/api/models/metrics", headers=headers)
        assert metrics_res.status_code == 200
        assert "social" in metrics_res.json()

        # 8. Persona & Social Settings
        persona_post = await client.post("/api/settings/persona", headers=headers, json={
            "identity_name": "LenAdmin",
            "identity_persona": "Custom test persona",
            "bot_qq": 987654321
        })
        assert persona_post.status_code == 200
        assert runtime.config.identity_name == "LenAdmin"
        assert runtime.config.bot_qq == 987654321

        social_post = await client.post("/api/settings/social", headers=headers, json={
            "monitored_keywords": ["测试", "直播"],
            "bot_cooldown_seconds": 120,
            "speaking_budget_base_threshold": 0.75,
            "interest_topics": {"gaming": 0.95, "tech": 0.8}
        })
        assert social_post.status_code == 200
        assert runtime.attention_engine.speaking_budget.base_threshold == 0.75
        assert runtime.attention_engine.interest_model.topics["gaming"] == 0.95

        # 9. Plugins Subsystem (real registry only, ADR-0021)
        plugins_get = await client.get("/api/plugins/list", headers=headers)
        assert plugins_get.status_code == 200
        plugins_list = plugins_get.json()
        assert {p["id"] for p in plugins_list} == {"bilibili_live_sensor", "web_search_tool"}
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
