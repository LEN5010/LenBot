"""Current persona's saved-file and exposed-tool permission boundaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from len_bot.next.chat import Chat
from len_bot.next.config import load_config
from len_bot.next.model import ChatModel, ToolCall
from len_bot.next.persona import load_persona
from len_bot.next.persona_knowledge import PersonaKnowledgeArguments, persona_knowledge
from len_bot.next.store import Store


def _package(root: Path, name: str, *, tools: str | list[str], documents: dict[str, str]) -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "persona.yaml").write_text(json.dumps({
        "id": name, "name": f"合成角色{name}", "brief": "仅隔离权限边界。",
        "behavior": "正常对话。", "self_reference": ["我"], "aliases": [],
        "tools": tools, "skills": [], "styles": [],
    }, ensure_ascii=False), encoding="utf-8")
    (package / "voice.md").write_text("简短表达。", encoding="utf-8")
    (package / "boundaries.md").write_text("隔离合成角色。", encoding="utf-8")
    (package / "examples.yaml").write_text("[]\n", encoding="utf-8")
    for filename, content in documents.items():
        path = package / "knowledge" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return package


def _config(root: Path, package: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "lenbot.config.json").write_text(json.dumps({
        "mode": "isolated", "scene": "group:80001", "bot_qq": "90001", "timezone": "UTC",
        "database": "persona.sqlite3", "persona": str(package), "voice_mode": "direct",
        "models": {
            "providers": {"synthetic": {"api": "openai-chat",
                                        "base_url": "http://127.0.0.1:9/v1",
                                        "api_key": "synthetic-unused-key"}},
            "roles": {
                "mind": {"provider": "synthetic", "model": "synthetic-mind",
                         "context_window_tokens": 8192},
                "voice": {"provider": "synthetic", "model": "synthetic-voice",
                          "context_window_tokens": 4096},
            },
        },
    }, ensure_ascii=False), encoding="utf-8")
    return load_config(root)


def test_loaded_persona_documents_cannot_cross_packages_or_escape_by_filename(tmp_path: Path) -> None:
    alpha_text = "---\ntags: [合成甲] \n---\n甲的合成设定，日期与原话仍在文件里。\n"
    beta_text = "---\ntags: [合成乙]\n---\n乙的独立设定，不属于甲。\n"
    alpha_path = _package(tmp_path, "alpha", tools=["tool_search", "persona_knowledge"],
                          documents={"shared.md": alpha_text, "lore/inside.md": "甲的子目录合成资料。\n"})
    beta_path = _package(tmp_path, "beta", tools=["tool_search", "persona_knowledge"],
                         documents={"shared.md": beta_text, "private.md": "乙的私有合成资料。\n"})
    alpha, beta = load_persona(alpha_path), load_persona(beta_path)

    assert alpha.knowledge["shared.md"].content == alpha_text
    assert beta.knowledge["shared.md"].content == beta_text
    assert "lore/inside.md" in alpha.knowledge and "private.md" not in alpha.knowledge
    alpha_page = persona_knowledge(alpha.id, alpha.name, alpha.knowledge,
                                   PersonaKnowledgeArguments(action="read", filename="shared.md"))
    beta_page = persona_knowledge(beta.id, beta.name, beta.knowledge,
                                  PersonaKnowledgeArguments(action="read", filename="shared.md"))
    assert "甲的合成设定" in alpha_page and "乙的独立设定" not in alpha_page
    assert "乙的独立设定" in beta_page and "甲的合成设定" not in beta_page
    (alpha_path / "knowledge" / "added-after-load.md").write_text("未加载的合成文件。\n", encoding="utf-8")
    for filename in ("private.md", "added-after-load.md", "../beta/knowledge/private.md",
                     str(beta_path / "knowledge" / "private.md")):
        with pytest.raises(ValueError):
            persona_knowledge(alpha.id, alpha.name, alpha.knowledge,
                              PersonaKnowledgeArguments(action="read", filename=filename))


def test_role_load_rejects_external_links_but_accepts_a_file_link_inside_knowledge(tmp_path: Path) -> None:
    external = _package(tmp_path, "external", tools="all",
                        documents={"private.md": "外部角色独立合成内容。\n"})
    inside = _package(tmp_path, "inside", tools="all",
                      documents={"base.md": "包内实际合成资料。\n"})
    (inside / "knowledge" / "alias.md").symlink_to(inside / "knowledge" / "base.md")
    loaded = load_persona(inside)
    assert loaded.knowledge["alias.md"].content == loaded.knowledge["base.md"].content

    escaped = _package(tmp_path, "escaped", tools="all", documents={})
    (escaped / "knowledge").mkdir()
    (escaped / "knowledge" / "outside.md").symlink_to(external / "knowledge" / "private.md")
    with pytest.raises(ValueError, match="knowledge|outside"):
        load_persona(escaped)

    linked_root = _package(tmp_path, "linked-root", tools="all", documents={})
    (linked_root / "knowledge").symlink_to(external / "knowledge", target_is_directory=True)
    with pytest.raises(ValueError, match="knowledge"):
        load_persona(linked_root)


@pytest.mark.asyncio
async def test_chat_only_exposes_current_role_documents_after_allowed_discovery(tmp_path: Path) -> None:
    async def wait_for_messages(_: float) -> str:
        return "unused"

    no_docs = _package(tmp_path, "empty", tools="all", documents={})
    explicit_missing = _package(tmp_path, "explicit-missing",
                                tools=["tool_search", "persona_knowledge"], documents={})
    no_search = _package(tmp_path, "no-search", tools=["persona_knowledge"],
                         documents={"one.md": "仅合成权限资料。\n"})
    allowed = _package(tmp_path, "allowed", tools=["tool_search", "persona_knowledge"],
                       documents={"one.md": "获准角色的合成资料。\n"})
    forbidden = _package(tmp_path, "forbidden", tools=[],
                         documents={"one.md": "无权限角色的合成资料。\n"})
    config = _config(tmp_path / "instance", allowed)

    with Store(config.database) as store:
        async with ChatModel(config.model_settings("mind")) as mind, ChatModel(
            config.model_settings("voice")
        ) as voice:
            chat = Chat(config, load_persona(no_docs), store, mind, voice)
            assert "persona_knowledge" not in chat.tool_names
            with pytest.raises(ValueError, match="persona_knowledge|knowledge"):
                Chat(config, load_persona(explicit_missing), store, mind, voice)
            with pytest.raises(ValueError, match="tool_search"):
                Chat(config, load_persona(no_search), store, mind, voice)

            chat = Chat(config, load_persona(allowed), store, mind, voice)
            assert "tool_search" in chat.tool_names and "persona_knowledge" not in chat.tool_names
            read = ToolCall(id="synthetic-read", name="persona_knowledge",
                            arguments={"action": "read", "filename": "one.md"})
            with pytest.raises(ValueError, match="persona_knowledge"):
                await chat.execute_tool("unused-turn", read, wait_for_messages)

            search = ToolCall(id="synthetic-search", name="tool_search",
                              arguments={"query": "persona_knowledge"})
            result, expression, discovered = await chat.execute_tool(
                "unused-turn", search, wait_for_messages,
            )
            assert expression is None and "persona_knowledge" in discovered
            assert "persona_knowledge" not in chat.tool_names  # same request still cannot use it
            store.complete_tool(config.scene, search.id, result, discovered_tools=discovered)
            chat = Chat(config, load_persona(allowed), store, mind, voice)
            assert "persona_knowledge" in chat.tool_names
            content, expression, discovered = await chat.execute_tool(
                "unused-turn", read, wait_for_messages,
            )
            assert "获准角色的合成资料" in content
            assert "无权限角色的合成资料" not in content
            assert expression is None and discovered is None

            # Persisted discovery is not permission: a different loaded role loses access.
            chat = Chat(config, load_persona(forbidden), store, mind, voice)
            assert "persona_knowledge" not in chat.tool_names
            with pytest.raises(ValueError, match="persona_knowledge"):
                await chat.execute_tool("unused-turn", read, wait_for_messages)
