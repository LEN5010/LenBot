"""Assemble scene prompt material from role files and existing conversation records."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo

from ..media.audio_store import AudioStore
from ..config import LabConfig
from .recap import project_history
from ..models.projection import project_messages, project_old_results
from ..tools.discovery import DEFERRED_NAMES
from ..learning.jargon_store import JargonStore
from ..memory.service import MemoryService
from ..platform.messages import ChatMessage, plain_text, render_batch, render_message, render_text
from ..persona.profile import Persona, select_examples
from .schedule import describe
from ..tools.skills import Skill
from ..storage.store import Store, encode
from ..work.store import TaskStore
from .schedule_store import ScheduleStore


PROMPTS = Path(__file__).resolve().parents[2] / "prompts"


def character_material(config: LabConfig, persona: Persona) -> str:
    scene_details = {}
    if config.persona_aliases:
        scene_details["本场景对你的称呼"] = config.persona_aliases
    if config.relationships:
        scene_details["关系说明（账号 → 描述）"] = dict(sorted(config.relationships.items()))
    if config.behavior_addendum is not None:
        scene_details["本场景行为补充"] = config.behavior_addendum
    scene_material = (Template((PROMPTS / "next_scene_persona.md").read_text()).substitute(
        details=encode(scene_details)) if scene_details else "")
    return Template((PROMPTS / "next_character.md").read_text()).substitute(
        brief=persona.brief, self_reference="、".join(persona.self_reference),
        aliases="、".join(persona.aliases), behavior=persona.behavior, voice=persona.voice,
        boundaries=persona.boundaries,
        examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in select_examples(persona)),
        scene_material=scene_material,
    )


def expression_principles(persona: Persona) -> str:
    return Template((PROMPTS / "next_expression_principles.md").read_text()).substitute(name=persona.name)


def build_system(config: LabConfig, persona: Persona, allowed: list[dict], *, platform: bool,
                 skills: tuple[Skill, ...] = (),
                 external: list[dict] = (), discovered: Sequence[str] = ()) -> str:
    """Render the actual stable mind system text for this scene and outlet."""
    allowed_names = {tool["function"]["name"] for tool in allowed}
    deferred_names = DEFERRED_NAMES if "tool_search" in allowed_names else frozenset()
    names = (allowed_names - deferred_names) | (set(discovered) & allowed_names)
    deferred = [tool for tool in allowed if tool["function"]["name"] in deferred_names] + list(external)
    system = Template((PROMPTS / "next_mind.md").read_text()).substitute(
        name=persona.name, scene=config.scene, bot_id=config.bot_id,
        character=character_material(config, persona),
        response_choice=Template((PROMPTS / "next_response_choice.md").read_text()).substitute(name=persona.name),
        expression_principles=expression_principles(persona),
        outlet=(PROMPTS / ("next_platform_outlet.md" if platform else
                           "next_simulated_outlet.md")).read_text().strip(),
    )
    system += "\n" + (PROMPTS / "next_chat_examples.md").read_text()
    if "react" in names:
        system += "\n" + (PROMPTS / "next_react.md").read_text()
    if "schedule" in names:
        system += "\n" + (PROMPTS / "next_schedule.md").read_text()
    if "memory" in names:
        system += "\n" + Template((PROMPTS / "next_memory.md").read_text()).substitute(
            backend=config.memory.backend,
            backend_details=(PROMPTS / f"next_memory_{config.memory.backend}.md").read_text(),
        )
    elif config.memory is not None and config.memory.auto_recall:
        system += "\n" + (PROMPTS / "next_memory_recall.md").read_text()
    if "task" in names:
        system += "\n" + Template((PROMPTS / "next_tasks.md").read_text()).substitute(
            network=encode({"enabled": config.worker.egress.enabled,
                           "max_task_bytes": (config.worker.egress.max_task_bytes
                               if config.tasks.egress_max_task_bytes is None else config.tasks.egress_max_task_bytes),
                           "max_daily_bytes": (config.worker.egress.max_scene_daily_bytes
                               if config.tasks.egress_max_daily_bytes is None else config.tasks.egress_max_daily_bytes)}),
        )
    if "send_file" in names:
        system += "\n" + (PROMPTS / "next_files.md").read_text()
    if 'host_manage' in names:
        system += '\n' + (PROMPTS / 'next_host_manage.md').read_text()
    if "delegate" in names and skills:
        system += "\n" + Template((PROMPTS / "next_skills.md").read_text()).substitute(
            catalog=encode([{"name": skill.name, "description": skill.description}
                            for skill in skills if not skill.disable_model_invocation]),
        )
    if "tool_search" in names:
        system += "\n" + Template((PROMPTS / "next_tools.md").read_text()).substitute(
            catalog="\n".join(f"- {tool['function']['name']}：{tool['function']['description'].split('。', 1)[0]}"
                              for tool in deferred) or "（当前没有允许发现的低频工具）")
    return system


def jargon_context(config: LabConfig, store: Store, messages: list[ChatMessage], *,
                   intent: str | None = None) -> str | None:
    if config.learning is None:
        return None
    excluded = (config.bot_id, *config.attention.other_bot_ids)
    texts = [plain_text(message) for message in messages
             if not message.is_self and message.send_status == "received"
             and message.sender.uid not in excluded]
    if intent is not None:
        texts.append(intent)
    terms = JargonStore(store).matches(config.scene, texts, limit=10)
    if not terms:
        return None
    return Template((PROMPTS / "next_jargon_context.md").read_text()).substitute(
        jargon=encode([{"词": item["term"], "含义": item["meaning"], "来源": item["source"]} for item in terms]),
    )


def turn_state(config: LabConfig, store: Store, *, now: float,
               expression_style: str | None = None, recalled: str | None = None) -> dict:
    """Read the current scene's pending work and per-request reference material."""
    moment = datetime.fromtimestamp(now, ZoneInfo(config.timezone)).isoformat(timespec="seconds")
    state = {"role": "user", "content": f"本轮开始时间：{moment}"}
    muted_until = store.bot_muted_until(config.scene, config.bot_id)
    if muted_until is not None:
        state["content"] += "\n平台通知：当前 Bot 禁言至 " + datetime.fromtimestamp(
            muted_until, ZoneInfo(config.timezone)).isoformat(timespec="seconds")
    schedules = ScheduleStore(store).list_schedules(config.scene, limit=21)
    if schedules:
        state["content"] += "\n<未完成安排>\n" + "\n\n".join(
            describe(item, preview=True) for item in schedules[:20]) + "\n</未完成安排>"
        if len(schedules) > 20:
            state["content"] += "\n这里只列前 20 条；schedule_list 可继续查看。"
    tasks = TaskStore(store).list(config.scene, limit=21)
    if tasks:
        state["content"] += "\n<未完成工作>\n" + encode([
            {"id": item.id, "requester": item.requester, "status": item.status,
             **({"question": item.question} if item.question is not None else {})}
            for item in tasks[:20]]) + "\n</未完成工作>"
        if len(tasks) > 20:
            state["content"] += "\n这里只列前20项；task可继续查看。"
    if expression_style is not None:
        state["content"] += "\n" + expression_style
    if recalled is not None:
        state["content"] += "\n<相关长期记忆>\n" + recalled + "\n</相关长期记忆>"
    jargon = jargon_context(config, store, store.recent_context_messages(config.scene))
    if jargon is not None:
        state["content"] += "\n" + jargon
    state["content"] = Template((PROMPTS / "next_turn_state.md").read_text()).substitute(state=state["content"])
    return state


class ChatContext:
    """Scene materials shared by model requests, expression and panel views."""

    def __init__(self, config: LabConfig, persona: Persona, store: Store, *,
                 platform: bool, memory: MemoryService | None):
        self.config, self.persona, self.store = config, persona, store
        self.platform, self.memory = platform, memory
        self.system: str

    def configure_tools(self, allowed: list[dict], external: list[dict], *, skills: tuple[Skill, ...]) -> None:
        self.allowed, self.external, self.skills = allowed, external, skills
        self.refresh_tools()

    def refresh_tools(self) -> None:
        self.system = build_system(self.config, self.persona, self.allowed, platform=self.platform,
                                   skills=self.skills, external=self.external,
                                   discovered=self.store.load_discovered_tools(self.config.scene))

    def render(self, message: ChatMessage) -> str:
        quote = (None if message.reply_to is None else
                 self.store.find_message(message.scene, message.reply_to))
        return render_message(message, timezone=self.config.timezone, reply=quote,
                              audio=AudioStore(self.store).captions(message.scene, message.platform_message_id))

    def batch(self, messages: Sequence[ChatMessage], *, reason: str | None = None) -> str:
        records = AudioStore(self.store)
        return render_batch([
            (message, None if message.reply_to is None else
             self.store.find_message(message.scene, message.reply_to),
             records.captions(message.scene, message.platform_message_id)) for message in messages
        ], timezone=self.config.timezone, reason=reason)

    def render_text(self, message: ChatMessage) -> str:
        quote = (None if message.reply_to is None else
                 self.store.find_message(message.scene, message.reply_to))
        return render_text(message, reply=quote,
                           audio=AudioStore(self.store).captions(message.scene, message.platform_message_id))

    def project_entries(self, entries: list[tuple[int, dict]]) -> list[tuple[int, dict]]:
        return [(seq, projected) for seq, message in project_old_results(
            entries, self.config.compaction.keep_recent_tokens)
            for projected in project_messages([message], self.config.models.roles.mind.history_policy)]

    async def project(self, recap: str | None, entries: list[tuple[int, dict]], state: dict) -> list[dict]:
        self.refresh_tools()
        entries = self.project_entries(entries)
        profile = None if self.memory is None else await self.memory.read_group_profile(self.config.scene)
        if profile is not None:
            state = {**state, "content": state["content"] + "\n" + Template(
                (PROMPTS / "next_group_profile.md").read_text()).substitute(profile=profile.strip())}
        return [{"role": "system", "content": self.system}] + project_history(recap, entries) + [state]
