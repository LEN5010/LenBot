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
        relevant_memories: Optional[list[Any]] = None,
        ambient_items: Optional[list[Any]] = None,
        actor_profile: Optional[dict[str, Any]] = None,
        actor_memories: Optional[list[Any]] = None
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
            f"5. 如果当前存在活跃的对话线索 (Participation Thread)，且当前消息与该线索相关，你可以自然延续对话；如果话题已经转移或者群友在聊其他不属于你的事情，请保持 SILENCE 并在 social_state_proposal 中设置 thread_transition=\"close\"。\n"
            f"6. 如果提供了相关环境条目 (Relevant Ambient Items) 且它们与当前话题确实相关，你可以自然提及；它们与当前话题无关时，绝不主动传播其内容。\n"
            f"7. 如果你想记住某件以后可能有用的事（例如刚看到的信息），可以在 retained_item_proposals 中提出；它只是短期记忆，不会让你现在发言。\n"
            f"8. 对记忆中的认识以自然口吻引用（像朋友一样\"记得\"），严禁汇报式表达（如\"根据记录…\"）；只有对方质疑或明确要求证据时，才调用 search_messages / read_context 查证。\n"
        )

        # 2. Situation Package
        scene_info = "未知场景"
        if scene_state:
            thread_info = "None"
            if scene_state.current_thread:
                thread_info = (
                    f"ThreadID: {scene_state.current_thread.thread_id} | "
                    f"Topic: {scene_state.current_thread.topic} | "
                    f"Status: {scene_state.current_thread.status.value} | "
                    f"Participants: {scene_state.current_thread.participants} | "
                    f"InterveningMsg: {scene_state.current_thread.intervening_messages}"
                )
            scene_info = (
                f"SceneID: {scene_state.scene_id} (Version: {scene_state.version})\n"
                f"Activity: {scene_state.activity_level} | Bot Engagement: {scene_state.bot_engagement}\n"
                f"Active Topic: {scene_state.active_topic or 'None'}\n"
                f"Participation Thread: {thread_info}\n"
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

        ambient_info = "无相关环境条目"
        if ambient_items:
            lines = [f"- ({item.topic}) {item.summary}" for item in ambient_items]
            ambient_info = "\n".join(lines)

        # Person Card (ADR-0019 §11.1): display identity + per-person memories.
        actor_info = f"{stimulus.actor_id}"
        if actor_profile:
            display_name = actor_profile.get("card") or actor_profile.get("nickname") or stimulus.actor_id
            role = actor_profile.get("role") or "member"
            actor_info = f"{display_name} (ID: {stimulus.actor_id}, 群身份: {role})"
        actor_memories_info = "无对此人的具体认识"
        if actor_memories:
            lines = [f"- {m.human_readable_assertion} (确定度: {m.certainty.value})" for m in actor_memories]
            actor_memories_info = "\n".join(lines)

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
            f"【CURRENT ACTOR】\n"
            f"{actor_info}\n"
            f"对他的认识：\n{actor_memories_info}\n\n"
            f"【ACTIVE OPEN LOOPS】\n"
            f"{loops_info}\n\n"
            f"【RELEVANT BELIEFS & MEMORY】\n"
            f"{memories_info}\n\n"
            f"【RELEVANT AMBIENT ITEMS】\n"
            f"{ambient_info}\n\n"
            f"【RECENT RAW CHAT】\n"
            f"{chat_history}\n\n"
            f"【CURRENT STIMULUS】\n"
            f"From: {stimulus.actor_id}\n"
            f"Content:\n{stimulus.combined_text}\n"
            f"MentionBot: {stimulus.has_mention_bot} | ReplyBot: {stimulus.has_reply_bot}\n\n"
            f"请仔细审视当前情境，以标准 JSON 对象格式输出 EpisodeOutcome：\n"
            f'{{"disposition": "SILENCE" | "ACTION", "decision_reason": "peer already answered / direct response needed / ...", "message_proposals": [{{"content": "..."}}], "task_proposals": [], "memory_proposals": [], "resolve_open_loop_ids": [], "social_state_proposal": {{"topic": "...", "thread_transition": "keep"|"fade"|"close"}}, "retained_item_proposals": []}}\n'
            f"task_proposals 若是条件触发型任务（如\"开播叫我\"），设置 wake_event_type（如 \"LIVE_STARTED\"）并可省略 delay_seconds；"
            f"否则用 delay_seconds 指定相对到期时间。"
        )

        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content}
        ]
