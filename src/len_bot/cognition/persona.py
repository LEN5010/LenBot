"""Layered persona composition (ADR-0038 §6).

Identity is no longer two static strings: the prompt is composed from an
Identity Core (long-term behavioural tendencies), a short style line, the
adaptive self state, the per-scene group register, and stable voice
exemplars. The identity block is stable across calls, which keeps it a good
prompt-caching prefix.
"""

import time

from len_bot.cognition.session import GroupAgentSession
from len_bot.config import RuntimeConfig


def render_identity_block(config: RuntimeConfig) -> str:
    return (
        "【IDENTITY CORE】\n"
        f"名字:{config.identity_name}\n"
        f"背景与性格:{config.identity_persona.strip()}\n"
        f"{config.identity_core.strip()}\n"
        f"语言风格:{config.conversation_style.strip()}\n"
        f"【角色参考：身份设定与梗的语境】\n{config.character_context.strip()}\n"
    )


def render_self_state_block(session: GroupAgentSession) -> str:
    self_state = session.self_social_state
    last_spoke = ""
    if self_state.last_bot_message_at is not None:
        elapsed = max(0, int(time.time() - self_state.last_bot_message_at))
        last_spoke = f" | 你最近一条消息是 {elapsed} 秒前" if elapsed > 3 else " | 你刚说过话"
    return (
        "【ADAPTIVE SELF STATE】\n"
        f"参与状态:{self_state.engagement} | 发言倾向:{self_state.inclination_to_speak}"
        f" | 连续发言:{self_state.consecutive_bot_messages} 条{last_spoke}\n"
    )


def render_register_block(session: GroupAgentSession) -> str:
    return f"【GROUP REGISTER】(本群的说话节奏，供理解当前语境)\n{session.group_register.render()}\n"


def render_voice_examples_block(examples: list[dict]) -> str:
    if not examples:
        return ""
    lines = []
    for example in examples:
        context = (example.get("context") or "").strip()
        content = (example.get("content") or "").strip()
        if not content:
            continue
        if context:
            lines.append(f"- 语境:{context} → 你:{content}")
        else:
            lines.append(f"- 你:{content}")
    if not lines:
        return ""
    return (
        "【VOICE EXAMPLES】(运营编写的情境与表达参考，结合当前原话选择合适的说法)\n"
        + "\n".join(lines)
        + "\n"
    )
