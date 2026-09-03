from typing import Any, Optional
from len_bot.config import RuntimeConfig
from len_bot.events.models import Stimulus, Event
from len_bot.scenes.models import SceneState

class ContextAssembler:
    def __init__(self, config: RuntimeConfig):
        self.config = config

    def assemble(
        self,
        stimulus: Stimulus,
        scene_state: Optional[SceneState],
        raw_events: list[Event],
        active_open_loops: list[dict[str, Any]],
        allowed_scopes: list[str],
        relevant_memories: Optional[list[Any]] = None
    ) -> list[dict[str, str]]:
        # 1. System Prompt (Identity + Principles)
        system_content = (
            f"【IDENTITY】\n"
            f"你的名字是：{self.config.identity_name}\n"
            f"{self.config.identity_persona}\n\n"
            f"【RULES】\n"
            f"1. 你处于一个长周期运行的社会化社群中。保持自然、拟人化、不刻板。\n"
            f"2. 如果不需要发声、群友只是在闲聊且未问到你、或者你已经回答过了，请将 disposition 设为 SILENCE。\n"
            f"3. 严禁无意义的客套废话或充当人工智障复读机。\n"
            f"4. 如果你提出了疑问并期待对方回答，请设置 expect_reply=True 并指明 reply_target。\n"
        )

        # 2. Situation Package
        scene_info = "未知场景"
        if scene_state:
            scene_info = (
                f"SceneID: {scene_state.scene_id} (Version: {scene_state.version})\n"
                f"Activity: {scene_state.activity_level} | Bot Engagement: {scene_state.bot_engagement}\n"
                f"Active Topic: {scene_state.active_topic or 'None'}\n"
                f"Soft Annotations: {scene_state.soft_annotations}"
            )

        loops_info = "无未结挂起事务"
        if active_open_loops:
            lines = [f"- [ID: {l['id']}] 等待 {l['target_actor_id']} 回应意图: {l['intent']}" for l in active_open_loops]
            loops_info = "\n".join(lines)

        memories_info = "无特殊认识与偏好记忆"
        if relevant_memories:
            lines = [f"- {m.human_readable_assertion} (确定度: {m.certainty.value})" for m in relevant_memories]
            memories_info = "\n".join(lines)

        # Elastic Raw Context (keep last 20 events)
        recent_events = raw_events[-20:]
        chat_lines = []
        for e in recent_events:
            if e.raw_text:
                actor = "你(Bot)" if e.actor_id == f"user:{self.config.bot_qq}" else e.actor_id
                chat_lines.append(f"[{e.id}] {actor}: {e.raw_text}")
        chat_history = "\n".join(chat_lines) if chat_lines else "(暂无近期历史)"

        user_content = (
            f"【CURRENT SITUATION】\n"
            f"{scene_info}\n\n"
            f"【ACTIVE OPEN LOOPS】\n"
            f"{loops_info}\n\n"
            f"【RELEVANT BELIEFS & MEMORY】\n"
            f"{memories_info}\n\n"
            f"【RECENT RAW CHAT】\n"
            f"{chat_history}\n\n"
            f"【CURRENT STIMULUS】\n"
            f"From: {stimulus.actor_id}\n"
            f"Content:\n{stimulus.combined_text}\n"
            f"MentionBot: {stimulus.has_mention_bot} | ReplyBot: {stimulus.has_reply_bot}\n\n"
            f"请仔细审视当前情境，以标准 JSON 对象格式输出 EpisodeOutcome：\n"
            f'{{"disposition": "SILENCE" | "ACTION", "thought": "...", "message_proposals": [{{"content": "..."}}], "task_proposals": [], "memory_proposals": [], "resolve_open_loop_ids": []}}'
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ]
