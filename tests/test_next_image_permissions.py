"""Scene and configured-role boundaries for already received image positions."""

import json
from io import BytesIO

import pytest
from PIL import Image

from len_bot.next.chat import Chat
from len_bot.next.config import ImageSettings, load_config
from len_bot.next.images import LookArguments, execute_look
from len_bot.next.messages import parse_message
from len_bot.next.model import ChatModel, ToolCall
from len_bot.next.persona import load_persona
from len_bot.next.store import ImageAsset, Store


def _jpeg(color: str) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (4, 3), color).save(buffer, format="JPEG")
    return buffer.getvalue()


def _receive_image(store: Store, scene: str, message_id: str) -> None:
    kind, number = scene.split(":", 1)
    raw = {
        "post_type": "message", "message_type": kind,
        "self_id": 90001, "user_id": int(number), "message_id": message_id,
        "time": 1_700_000_000, "sender": {"nickname": "测试发送者", "role": "member"},
        "message": [{"type": "image", "data": {
            "file": "platform-image-file", "url": "https://example.com/image.jpg",
        }}],
    }
    if kind == "group":
        raw["group_id"] = int(number)
    message = parse_message(raw, own_message_ids=set())
    store.enqueue(message, raw, received_at=1_700_000_001.0)


def _asset(color: str, caption: str) -> ImageAsset:
    return ImageAsset(
        jpeg=_jpeg(color), width=4, height=3, animated=False,
        fetched_at=1_700_000_002.0, description=caption,
        description_model="synthetic-vision", described_at=1_700_000_003.0,
    )


def _chat_inputs(tmp_path, *, tools: str | list[str], vision: bool):
    root = tmp_path / "isolated"
    persona = root / "persona"
    persona.mkdir(parents=True)
    (persona / "persona.yaml").write_text(json.dumps({
        "id": "image-permissions", "name": "测试角色", "brief": "仅用于权限测试。",
        "behavior": "简短回答。", "self_reference": ["我"], "aliases": [],
        "tools": tools, "skills": [], "styles": [],
    }), encoding="utf-8")
    (persona / "voice.md").write_text("简短。", encoding="utf-8")
    (persona / "boundaries.md").write_text("仅限测试。", encoding="utf-8")
    (persona / "examples.yaml").write_text("[]\n", encoding="utf-8")
    roles = {
        "mind": {"provider": "local", "model": "synthetic-mind", "context_window_tokens": 8192},
        "voice": {"provider": "local", "model": "synthetic-voice", "context_window_tokens": 4096},
    }
    if vision:
        roles["vision"] = {
            "provider": "local", "model": "synthetic-vision", "context_window_tokens": 4096,
        }
    (root / "lenbot.config.json").write_text(json.dumps({
        "mode": "isolated", "scene": "group:80001", "bot_qq": "90001",
        "timezone": "UTC", "database": "data/image-permissions.sqlite3",
        "persona": "persona", "models": {
            "providers": {"local": {
                "api": "openai-chat", "base_url": "http://127.0.0.1:9/v1",
                "api_key": "synthetic-unused-key",
            }},
            "roles": roles,
        },
    }), encoding="utf-8")
    return load_config(root), load_persona(persona)


@pytest.mark.asyncio
async def test_cached_images_with_same_platform_id_do_not_cross_scenes(tmp_path):
    first, second, private, group_only = (
        "本群的合成红图描述", "另一个群的合成蓝图描述",
        "私聊的合成绿图描述", "其他群独有图片描述",
    )
    with Store(tmp_path / "images.sqlite3") as store:
        for scene, message_id, color, caption in (
            ("group:80001", "400", "red", first),
            ("group:80002", "400", "blue", second),
            ("group:80002", "401", "yellow", group_only),
            ("private:80003", "402", "green", private),
        ):
            _receive_image(store, scene, message_id)
            store.save_image(scene, message_id, 1, _asset(color, caption))

        assert store.image("group:80001", "400", 1).description == first
        assert store.image("group:80002", "400", 1).description == second
        assert store.image("group:80001", "401", 1) is None
        assert store.image("group:80001", "402", 1) is None

        async def cached_description(asset: ImageAsset) -> str:
            return asset.description or ""

        async def look(scene: str, message: str) -> str:
            return await execute_look(
                store, scene, LookArguments(message=message), ImageSettings(),
                model_name="synthetic-vision", describe=cached_description,
            )

        local_result = await look("group:80001", "400")
        other_result = await look("group:80002", "400")
        private_result = await look("private:80003", "402")
        for result in (local_result, other_result, private_result):
            observed = json.loads(result.split("\n", 1)[0])
            assert observed["pixels_reused"] is True
            assert observed["description_reused"] is True
        assert first in local_result and second not in local_result and private not in local_result
        assert group_only not in local_result
        assert second in other_result and first not in other_result
        assert private in private_result and first not in private_result

        for foreign_id in ("401", "402", "999"):
            with pytest.raises(ValueError) as failure:
                await look("group:80001", foreign_id)
            assert second not in str(failure.value)
            assert private not in str(failure.value)
            assert group_only not in str(failure.value)
        with pytest.raises(ValueError):
            store.save_image_description("group:80001", "401", 1, "越权改写", "synthetic-vision", 0.0)
        assert store.image("group:80002", "401", 1).description == group_only


@pytest.mark.asyncio
async def test_look_registration_requires_role_configuration_and_vision_client(tmp_path):
    async def wait_for_messages(seconds: float) -> str:
        return ""

    config, persona = _chat_inputs(tmp_path / "explicit", tools=["look"], vision=False)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(
            config.model_settings("voice")
        ) as voice:
            with pytest.raises(ValueError, match="look|vision"):
                Chat(config, persona, store, mind, voice)

    config, persona = _chat_inputs(tmp_path / "all", tools="all", vision=False)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(
            config.model_settings("voice")
        ) as voice:
            chat = Chat(config, persona, store, mind, voice)
            assert "look" not in chat.toolset.tool_names

    config, persona = _chat_inputs(tmp_path / "missing-client", tools=["look"], vision=True)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(
            config.model_settings("voice")
        ) as voice:
            with pytest.raises(ValueError, match="look|vision"):
                Chat(config, persona, store, mind, voice)

    config, persona = _chat_inputs(tmp_path / "allowed", tools=["look"], vision=True)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(
            config.model_settings("voice")
        ) as voice, ChatModel(config.model_settings("vision")) as vision:
            chat = Chat(config, persona, store, mind, voice, vision=vision)
            assert "look" in chat.toolset.tool_names
            _receive_image(store, config.scene, "500")
            store.save_image(config.scene, "500", 1, _asset("purple", "获准场景的已缓存描述"))
            content, expression, discovered = await chat.toolset.execute(
                "unused-turn", ToolCall(id="look-allowed", name="look",
                                        arguments={"message": "500", "image": 1}),
                wait_for_messages,
            )
            assert "获准场景的已缓存描述" in content
            assert expression is None and discovered is None

    config, persona = _chat_inputs(tmp_path / "forbidden", tools=[], vision=True)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(
            config.model_settings("voice")
        ) as voice, ChatModel(config.model_settings("vision")) as vision:
            chat = Chat(config, persona, store, mind, voice, vision=vision)
            assert "look" not in chat.toolset.tool_names
            with pytest.raises(ValueError, match="look"):
                await chat.toolset.execute("unused-turn", ToolCall(
                    id="unused-call", name="look", arguments={"message": "400", "image": 1},
                ), wait_for_messages)
