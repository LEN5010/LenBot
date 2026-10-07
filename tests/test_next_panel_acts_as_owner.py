"""The logged-in host panel acts as the first configured owner instead of a typed-in account."""

import asyncio
import json
from pathlib import Path

import httpx

from len_bot.next.config import load_host_config
from len_bot.next.models.client import ChatModel
from len_bot.next.panel.app import create_app
from len_bot.next.persona.profile import load_persona
from len_bot.next.runtime.network import NetworkRuntime
from len_bot.next.storage.store import Store
from len_bot.web.auth import hash_password

SCENE = "onebot:group:80001"


def write_instance(root: Path, owners: list[str]) -> None:
    role = root / "persona"
    role.mkdir(parents=True)
    (role / "persona.yaml").write_text(
        "id: synthetic-owner\nname: 合成角色\nbrief: 面板身份测试。\nbehavior: 不主动表达。\n"
        "self_reference: [我]\naliases: []\ntools: [tool_search, schedule, schedule_list, schedule_cancel]\nskills: []\nstyles: []\n", encoding="utf-8")
    (role / "voice.md").write_text("简短。", encoding="utf-8")
    (role / "boundaries.md").write_text("仅限隔离测试。", encoding="utf-8")
    (role / "examples.yaml").write_text("[]\n", encoding="utf-8")
    source = {
        "compaction": {"input_tokens": 2000}, "mode": "isolated-multi", "bot_id": "onebot:90001", "timezone": "UTC", "owners": owners, "onebot": None,
        "database": "host.sqlite3", "delivery": "simulated", "logging": {"directory": "logs"},
        "panel": {"host": "127.0.0.1", "port": 0, "username": "host-operator",
                  "password_hash": hash_password("synthetic-password", salt="synthetic-salt")},
        "models": {
            "providers": {"local": {"api": "openai-chat", "base_url": "http://127.0.0.1:9/v1", "api_key": "synthetic-unused"}},
            "roles": {"mind": {"provider": "local", "model": "synthetic-mind", "context_window_tokens": 8192}},
        },
        "scenes": {SCENE: {"persona": "persona"}},
    }
    (root / "lenbot.config.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")


def exercise(root: Path, check) -> None:
    config = load_host_config(root)
    persona = load_persona(config.scenes[SCENE].persona)

    async def run() -> None:
        with Store(config.database) as store:
            async with ChatModel(config.model_settings("mind")) as mind:
                runtime = NetworkRuntime(config, [(config.scene_config(SCENE), persona)], store, mind)
                app = create_app(config, runtime, root=root)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
                    assert (await client.post("/api/auth/login", json={
                        "username": "host-operator", "password": "synthetic-password"})).status_code == 200
                    await check(client)

    asyncio.run(run())


REMINDER = {"when": "2099-01-01T08:00:00+00:00", "note": "合成提醒", "for": "self"}


def test_panel_schedules_are_owned_by_first_owner(tmp_path: Path) -> None:
    write_instance(tmp_path, ["onebot:70001", "onebot:70002"])

    async def check(client: httpx.AsyncClient) -> None:
        typed = await client.post(f"/api/host/schedules?scene={SCENE}", json={**REMINDER, "requester": "onebot:70002"})
        assert typed.status_code == 422
        created = await client.post(f"/api/host/schedules?scene={SCENE}", json=REMINDER)
        assert created.status_code == 200, created.text
        assert created.json()["requester"] == "onebot:70001"
        cancelled = await client.post(f"/api/host/schedules/{created.json()['id']}/cancel?scene={SCENE}")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

    exercise(tmp_path, check)


def test_panel_without_owner_refuses_owned_actions(tmp_path: Path) -> None:
    write_instance(tmp_path, [])

    async def check(client: httpx.AsyncClient) -> None:
        response = await client.post(f"/api/host/schedules?scene={SCENE}", json=REMINDER)
        assert response.status_code == 409
        assert "主人账号" in response.json()["detail"]

    exercise(tmp_path, check)
