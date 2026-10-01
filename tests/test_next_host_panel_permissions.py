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
                        "mode": "isolated-multi", "home": "/host/overview",
                    }
                    for path in ("/api/auth/me", "/api/host/state", "/api/host/retention", "/api/host/limits", "/api/host/scenes/group:80001/control", "/api/host/scenes/group:80001",
                                 f"/api/host/scenes/group:80001/turns/{configured_turn}"):
                        assert (await client.get(path)).status_code == 401

                    assert (await client.post("/api/host/trials", json={
                        "scene": "group:80001", "acknowledge_model_cost": True,
                    })).status_code == 401

                    assert (await client.get("/api/host/memory/native-overview", params={"scene":"group:80001"})).status_code == 401
                    assert (await client.post("/api/host/memory/native-overview", json={"scene":"group:80001"})).status_code == 401

                    login = await client.post("/api/auth/login", json={
                        "username": "host-operator", "password": "synthetic-password",
                    })
                    assert login.status_code == 200
                    assert (await client.post("/api/host/retention")).status_code == 422
                    assert (await client.post("/api/host/retention?confirmed=true")).status_code == 422
                    assert (await client.post("/api/host/scenes/group:89999/control/quiet", json={"seconds":60})).status_code == 404
                    assert (await client.post("/api/host/scenes/group:80001/control/quiet", json={"seconds":0})).status_code == 422
                    async with runtime.runners['group:80001'].execution:
                        assert (await client.post("/api/host/scenes/group:80001/control/resume")).status_code == 409
                    saved = (root / "lenbot.config.json").read_bytes()
                    assert (await client.put("/api/host/settings/retention", json={
                        "retention": {"timeline_days": 31},
                    })).status_code == 422
                    assert (root / "lenbot.config.json").read_bytes() == saved
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
                    browser_settings = {'socket':str(root/'browser.sock'), 'browser_instance_id':None,
                                        'binary':str(root/'bsk'), 'home':str(root/'browser-home'),
                                        'timeout_seconds':65, 'max_response_bytes':16000000}
                    browser_saved = await client.put('/api/host/browser', json={'settings':browser_settings})
                    assert browser_saved.status_code == 200, browser_saved.text
                    assert browser_saved.json()['running'] is None and browser_saved.json()['restart_required']
                    assert browser_saved.json()['saved'] == browser_settings
                    invalid_browser = {**browser_settings, 'socket':'relative.sock'}
                    assert (await client.put('/api/host/browser', json={'settings':invalid_browser})).status_code == 422

                    permissions = (await client.get('/api/host/permissions?scene=group:80001')).json()
                    change = permissions['saved']
                    change['global_identities']['admins'] = ['70003']
                    change['scene_identities'] = {'admins':[], 'whitelist':['70004'], 'blacklist':['70005']}
                    change['task_identities'] = {'owner':'70006', 'admins':['70007'], 'whitelist':[]}
                    saved = await client.put('/api/host/permissions?scene=group:80001', json=change)
                    assert saved.status_code == 200, saved.text
                    assert saved.json()['restart_required'] is True
                    assert saved.json()['running']['global_identities']['admins'] == []
                    assert load_host_config(root).scene_config('group:80001').permissions.admins == ['70003']
                    saved_tasks = load_host_config(root).scenes['group:80001'].tasks
                    assert (saved_tasks.owner, saved_tasks.admins) == ('70006', ['70007'])
                    pending = (await client.get('/api/host/pending-restart')).json()
                    assert {'permissions', 'account_browser'} <= set(pending['sections'])
                    assert pending['scenes'] == ['group:80001'] and pending['personas'] == []
                    scene_saved = (await client.get('/api/host/settings')).json()['saved']['scenes']['group:80001']
                    scene_body = {'timezone': None, 'voice_mode': 'direct', 'attention': scene_saved['attention'],
                                  'schedules': {'enabled': True, 'max_pending': 9, 'autonomous': False},
                                  'proactive': None, 'transcribe_audio': False, 'persona_aliases': [],
                                  'relationships': {}, 'behavior_addendum': None}
                    assert (await client.put('/api/host/settings/scenes/group:80001', json=scene_body)).status_code == 200
                    task_body = {'enabled': False, 'max_running': 1, 'max_daily_tasks': 3, 'egress_max_task_bytes': None,
                                 'egress_max_daily_bytes': None, 'egress_bytes_per_second': None}
                    assert (await client.put('/api/host/settings/scenes/group:80001/tasks', json={'tasks': task_body})).status_code == 200
                    kept = load_host_config(root).scenes['group:80001']
                    assert (kept.schedules.max_pending, kept.schedules.manage) == (9, change['matrix']['reminder_manage'])
                    assert (kept.tasks.max_running, kept.tasks.owner) == (1, '70006')
                    with_roles = {**scene_body, 'schedules': {**scene_body['schedules'], 'own': ['owner']}}
                    assert (await client.put('/api/host/settings/scenes/group:80001', json=with_roles)).status_code == 422
                    bad_scoped = {**change, 'schedule_identities': {'owner':'0', 'admins':[], 'whitelist':[]}}
                    assert (await client.put('/api/host/permissions?scene=group:80001', json=bad_scoped)).status_code == 422
                    assert load_host_config(root).scene_config('group:80002').permissions.whitelist == []
                    invalid_change = {**change, 'global_identities': {'admins':['70003','70003'], 'whitelist':[], 'blacklist':[]}}
                    assert (await client.put('/api/host/permissions?scene=group:80001', json=invalid_change)).status_code == 422

                    assert (await client.post("/api/host/trials", json={
                        "scene": "group:89999", "acknowledge_model_cost": True,
                    })).status_code == 404
                    assert (await client.post("/api/host/trials", json={
                        "scene": "group:80001", "acknowledge_model_cost": False,
                    })).status_code == 422
                    trial = await client.post("/api/host/trials", json={
                        "scene": "group:80001", "acknowledge_model_cost": True, "context_messages": 20,
                    })
                    assert trial.status_code == 200
                    assert len(trial.json()['context']) == 1
                    assert '隔离测试原文' in trial.json()['context'][0]
                    prefix = f"/api/host/trials/{trial.json()['id']}"
                    try:
                        assert (await client.get(f"{prefix}/turns/{configured_turn}")).status_code == 404
                        assert (await client.post(f"{prefix}/messages", json={
                            "uid": "90001", "nickname": "虚拟", "text": "合成消息",
                        })).status_code == 422
                        assert (await client.post(f"{prefix}/messages", json={
                            "uid": "70001", "nickname": "虚拟", "text": "合成消息",
                            "reply_to": "10001",
                        })).status_code == 422
                        assert (await client.post("/api/auth/logout")).status_code == 200
                        for method, path in (("GET", prefix + "/state"),
                                             ("POST", prefix + "/stop"),
                                             ("GET", "/api/host/trials"), ("GET", "/api/host/permissions?scene=group:80001")):
                            assert (await client.request(method, path)).status_code == 401
                    finally:
                        await app.state.trials.close()
                    assert (await client.get("/api/host/state")).status_code == 401
                await runtime.platform.close()

    asyncio.run(exercise())
