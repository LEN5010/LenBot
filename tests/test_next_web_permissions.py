"""Scene and configured-role access to saved web-reading documents."""

import json

import pytest

from len_bot.next.chat.session import Chat
from len_bot.next.configuration.chat import WebReadSettings
from len_bot.next.config import load_config
from len_bot.next.models.client import ChatModel, ToolCall
from len_bot.next.persona.profile import load_persona
from len_bot.next.storage.store import Store, WebPage
from len_bot.next.tools.web_read import WebReadArguments, execute_web_read


def _page(content: str) -> WebPage:
    return WebPage(
        url="https://example.com/source", final_url="https://example.com/final",
        fetched_at=1_700_000_000.0, media_type="text/plain", content=content,
        notice="测试资料，仅作资料阅读。",
    )


def _chat_inputs(tmp_path, *, tools: str | list[str], web_read: dict | None):
    root = tmp_path / "isolated"
    persona = root / "persona"
    persona.mkdir(parents=True)
    (persona / "persona.yaml").write_text(json.dumps({
        "id": "web-permissions", "name": "测试角色", "brief": "仅用于权限测试。",
        "behavior": "简短回答。", "self_reference": ["我"], "aliases": [],
        "tools": tools, "skills": [], "styles": [],
    }), encoding="utf-8")
    (persona / "voice.md").write_text("简短。", encoding="utf-8")
    (persona / "boundaries.md").write_text("仅限测试。", encoding="utf-8")
    (persona / "examples.yaml").write_text("[]\n", encoding="utf-8")
    (root / "lenbot.config.json").write_text(json.dumps({"compaction": {"input_tokens": 2000},
        "mode": "isolated", "scene": "onebot:group:80001", "bot_id": 'onebot:90001',
        "timezone": "UTC", "database": "data/web-permissions.sqlite3",
        "persona": "persona", "web_read": web_read,
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
    }), encoding="utf-8")
    return load_config(root), load_persona(persona)


@pytest.mark.asyncio
async def test_saved_web_document_is_readable_only_in_its_scene(tmp_path):
    local_content = "本场景文档的独有正文。"
    group_secret = "其他群的私有正文标记。"
    private_secret = "私聊的私有正文标记。"
    with Store(tmp_path / "web-permissions.sqlite3") as store:
        local_id = store.save_web_page("onebot:group:80001", _page(local_content))
        group_id = store.save_web_page("onebot:group:80002", _page(group_secret))
        private_id = store.save_web_page("onebot:private:80003", _page(private_secret))

        assert store.web_page("onebot:group:80001", local_id) == _page(local_content)
        assert store.web_page("onebot:group:80001", group_id) is None
        assert store.web_page("onebot:group:80001", private_id) is None
        assert store.web_page("onebot:group:80001", 999_999) is None

        settings = WebReadSettings()
        result = await execute_web_read(
            store, "onebot:group:80001", settings, WebReadArguments(document=local_id, offset=0)
        )
        assert local_content in result

        for document in (group_id, private_id, 999_999):
            with pytest.raises(ValueError) as failure:
                await execute_web_read(
                    store, "onebot:group:80001", settings,
                    WebReadArguments(document=document, offset=0),
                )
            assert group_secret not in str(failure.value)
            assert private_secret not in str(failure.value)
            assert local_content not in str(failure.value)


@pytest.mark.asyncio
async def test_web_read_registration_requires_configuration_and_role(tmp_path):
    config, persona = _chat_inputs(tmp_path / "explicit", tools=["web_read"], web_read=None)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind:
            with pytest.raises(ValueError, match="web_read"):
                Chat(config, persona, store, mind)

    config, persona = _chat_inputs(tmp_path / "all", tools="all", web_read=None)
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind:
            chat = Chat(config, persona, store, mind)
            assert "web_read" not in {tool["function"]["name"] for tool in chat.toolset.tools}

    config, persona = _chat_inputs(tmp_path / "allowed", tools=["web_read"], web_read={})
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind:
            chat = Chat(config, persona, store, mind)
            assert "web_read" in {tool["function"]["name"] for tool in chat.toolset.tools}

    config, persona = _chat_inputs(tmp_path / "forbidden", tools=[], web_read={})
    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind:
            chat = Chat(config, persona, store, mind)
            assert "web_read" not in {tool["function"]["name"] for tool in chat.toolset.tools}

            async def wait_for_messages(seconds: float) -> str:
                return ""

            with pytest.raises(ValueError, match="web_read"):
                await chat.toolset.execute("unused-turn", ToolCall(
                    id="unused-call", name="web_read", arguments={"document": 1, "offset": 0},
                ), wait_for_messages)
