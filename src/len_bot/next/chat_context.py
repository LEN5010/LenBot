"""Assemble scene prompt material from role files and existing conversation records."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from string import Template
from zoneinfo import ZoneInfo

from .config import LabConfig
from .discovery import DEFERRED_NAMES
from .jargon_store import JargonStore
from .messages import ChatMessage, plain_text
from .persona import Persona, select_examples
from .schedule import describe
from .skills import Skill
from .store import Store, encode
from .tasks_store import TaskStore


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def voice_prompt(persona: Persona, *, platform: bool) -> str:
    return Template((PROMPTS / "next_voice.md").read_text()).substitute(
        name=persona.name, brief=persona.brief,
        outlet=(PROMPTS / ("next_platform_outlet.md" if platform else "next_simulated_outlet.md")).read_text().strip(),
        self_reference="、".join(persona.self_reference), voice=persona.voice,
        boundaries=persona.boundaries,
        examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in select_examples(persona)),
    )


def build_system(config: LabConfig, persona: Persona, allowed: list[dict], *, platform: bool,
                 skills: tuple[Skill, ...] = (), group_profile: str | None = None,
                 external: list[dict] = ()) -> str:
    """Render the actual stable mind system text for this scene and outlet."""
    names = {tool["function"]["name"] for tool in allowed}
    deferred = [tool for tool in allowed if tool["function"]["name"] in DEFERRED_NAMES] + list(external)
    mode = "next_direct.md" if config.voice_mode == "direct" else "next_intent.md"
    expression_mode = Template((PROMPTS / mode).read_text()).substitute(
        voice=persona.voice,
        examples="\n\n".join(f"{e.context}\n台词：{e.line}" for e in select_examples(persona)))
    system = Template((PROMPTS / "next_mind.md").read_text()).substitute(
        name=persona.name, scene=config.scene, bot_qq=config.bot_qq,
        brief=persona.brief, self_reference="、".join(persona.self_reference),
        aliases="、".join(persona.aliases), behavior=persona.behavior,
        boundaries=persona.boundaries, expression_mode=expression_mode,
        outlet=(PROMPTS / ("next_platform_outlet.md" if platform else
                           "next_simulated_outlet.md")).read_text().strip(),
    )
    scene_details = {}
    if config.persona_aliases:
        scene_details["本场景对你的称呼"] = config.persona_aliases
    if config.relationships:
        scene_details["关系说明（QQ → 描述）"] = dict(sorted(config.relationships.items()))
    if config.behavior_addendum is not None:
        scene_details["本场景行为补充"] = config.behavior_addendum
    if scene_details:
        system += "\n" + Template((PROMPTS / "next_scene_persona.md").read_text()).substitute(
            details=encode(scene_details),
        )
    if group_profile is not None:
        system += "\n" + Template((PROMPTS / "next_group_profile.md").read_text()).substitute(
            profile=group_profile.strip())
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
    if "delegate" in names and skills:
        system += "\n" + Template((PROMPTS / "next_skills.md").read_text()).substitute(
            catalog=encode([{"name": skill.name, "description": skill.description}
                            for skill in skills if not skill.disable_model_invocation]),
        )
    if "tool_search" in names:
        system += "\n" + Template((PROMPTS / "next_tools.md").read_text()).substitute(
            catalog="\n".join(f"- {tool['function']['name']}：{tool['function']['description'].split('；')[0]}"
                              for tool in deferred) or "（当前没有允许发现的低频工具）")
    return system


def jargon_context(config: LabConfig, store: Store, messages: list[ChatMessage], *,
                   intent: str | None = None) -> str | None:
    if config.learning is None:
        return None
    excluded = (config.bot_qq, *config.attention.other_bot_qqs)
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
    state = {"role": "user", "content": f"当前时间：{moment}"}
    schedules = store.list_schedules(config.scene, limit=21)
    if schedules:
        state["content"] += "\n<未完成安排>\n" + "\n\n".join(
            describe(item, preview=True) for item in schedules[:20]) + "\n</未完成安排>"
        if len(schedules) > 20:
            state["content"] += "\n这里只列前 20 条；schedule_list 可继续查看。"
    tasks = TaskStore(store).list(config.scene, limit=21)
    if tasks:
        state["content"] += "\n<未完成工作>\n" + encode([
            {"id": item.id, "requester": item.requester, "goal": item.goal,
             "status": item.status, "question": item.question} for item in tasks[:20]]) + "\n</未完成工作>"
        if len(tasks) > 20:
            state["content"] += "\n这里只列前20项；task可继续查看。"
    if config.voice_mode == "direct" and expression_style is not None:
        state["content"] += "\n" + expression_style
    if recalled is not None:
        state["content"] += "\n<相关长期记忆>\n" + recalled + "\n</相关长期记忆>"
    jargon = jargon_context(config, store, store.recent_context_messages(config.scene))
    if jargon is not None:
        state["content"] += "\n" + jargon
    return state
