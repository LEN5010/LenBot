"""Authenticated read scope of the independent multi-scene host panel."""

import asyncio
import json
from pathlib import Path

import httpx

from len_bot.next.config import load_host_config
from len_bot.next.host_panel import create_app
from len_bot.next.messages import parse_message
from len_bot.next.model import ChatModel
from len_bot.next.network import NetworkRuntime
from len_bot.next.persona import load_persona
from len_bot.next.store import Store
from len_bot.web.auth import hash_password


def test_host_panel_only_reads_authenticated_configured_scenes(tmp_path: Path) -> None:
    root = tmp_path / "host"
    root.mkdir()
    role = root / "persona"
    role.mkdir()
    (role / "persona.yaml").write_text(
        "id: synthetic-host\nname: 合成角色\nbrief: 权限边界测试。\n"
        "behavior: 不主动表达。\nself_reference: [我]\naliases: []\n"
        "tools: []\nskills: []\nstyles: []\n", encoding="utf-8",
    )
    (role / "voice.md").write_text("简短。", encoding="utf-8")
    (role / "boundaries.md").write_text("仅限隔离测试。", encoding="utf-8")
    (role / "examples.yaml").write_text("[]\n", encoding="utf-8")
    source = {
        "mode": "isolated-multi", "bot_qq": "90001", "timezone": "UTC",
        "database": "host.sqlite3", "delivery": "simulated",
        "onebot": {"mode": "forward_ws", "ws_url": "ws://127.0.0.1:9"},
        "panel": {"host": "127.0.0.1", "port": 0, "username": "host-operator",
                  "password_hash": hash_password("synthetic-password", salt="synthetic-salt")},
        "models": {
            "providers": {"local": {"api": "openai-chat", "base_url": "http://127.0.0.1:9/v1",
                                    "api_key": "synthetic-unused"}},
            "roles": {
                "mind": {"provider": "local", "model": "synthetic-mind", "context_window_tokens": 8192},
                "voice": {"provider": "local", "model": "synthetic-voice", "context_window_tokens": 8192},
            },
        },
        "scenes": {
            "group:80001": {"persona": "persona"},
            "group:80002": {"persona": "persona", "voice_mode": "direct"},
        },
    }
    (root / "lenbot.config.json").write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    config = load_host_config(root)
    persona = load_persona(config.scenes["group:80001"].persona)

    async def exercise() -> None:
        with Store(config.database) as store:
            async with ChatModel(config.model_settings("mind")) as mind, \
                       ChatModel(config.model_settings("voice")) as voice:
                runtime = NetworkRuntime(
                    config, [(config.scene_config(scene), persona) for scene in config.scenes],
                    store, mind, voice,
                )
                configured_turn = store.start_turn("group:80001")
                store.end_turn(configured_turn, "settled")
                other_turn = store.start_turn("group:80002")
                store.end_turn(other_turn, "settled")
                leftover_turn = store.start_turn("group:89999")
                store.end_turn(leftover_turn, "settled")
                for number, scene in enumerate(("group:80001", "group:89999"), 1):
                    raw = {
                        "post_type": "message", "message_type": "group", "self_id": 90001,
                        "group_id": int(scene.split(":")[1]), "user_id": 70001,
                        "message_id": 10000 + number, "time": 1780000000 + number,
                        "sender": {"nickname": "合成发言者", "card": "", "role": "member"},
                        "message": [{"type": "text", "data": {"text": "隔离测试原文"}}],
                    }
                    store.enqueue(parse_message(raw, own_message_ids=set()), raw, 1780000000 + number)

                app = create_app(config, runtime, root=root)
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                    assert (await client.get("/api/panel-context")).json() == {
                        "mode": "isolated-multi", "home": "/host",
                    }
                    for path in ("/api/auth/me", "/api/host/state", "/api/host/scenes/group:80001",
                                 f"/api/host/scenes/group:80001/turns/{configured_turn}"):
                        assert (await client.get(path)).status_code == 401

                    login = await client.post("/api/auth/login", json={
                        "username": "host-operator", "password": "synthetic-password",
                    })
                    assert login.status_code == 200
                    state = (await client.get("/api/host/state")).json()
                    assert state["delivery"] == "simulated"
                    assert [item["scene"] for item in state["scenes"]] == ["group:80001", "group:80002"]
                    assert state["connection"]["connected"] is False

                    scene = (await client.get("/api/host/scenes/group:80001")).json()
                    assert scene["scene"] == "group:80001"
                    assert scene["persona"] == {"id": "synthetic-host", "name": "合成角色"}
                    assert len(scene["messages"]) == 1
                    assert scene["messages"][0]["rendered"]
                    assert [turn["id"] for turn in scene["turns"]] == [configured_turn]
                    assert (await client.get(f"/api/host/scenes/group:80001/turns/{configured_turn}")).status_code == 200
                    assert (await client.get("/api/host/scenes/group:89999")).status_code == 404
                    assert (await client.get(f"/api/host/scenes/group:89999/turns/{leftover_turn}")).status_code == 404
                    assert (await client.get(f"/api/host/scenes/group:80001/turns/{other_turn}")).status_code == 404
                    for method, path in (("POST", "/api/host/scenes/group:80001/messages"),
                                         ("PUT", "/api/host/scenes/group:80001"),
                                         ("DELETE", "/api/host/scenes/group:80001")):
                        assert (await client.request(method, path)).status_code in {404, 405}
                    assert (await client.post("/api/auth/logout")).status_code == 200
                    assert (await client.get("/api/host/state")).status_code == 401
                await runtime.platform.close()

    asyncio.run(exercise())
