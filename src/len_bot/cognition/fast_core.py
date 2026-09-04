"""FAST Social Cognition (ADR-0038): one-shot low-latency social decision.

A single small-model call that understands the local social context and either
stays silent, speaks final short messages, or escalates the burst to the FULL
SocialCognitionCore. It is NOT a classifier — no keyword/relevance rules feed
it; escalation is the model's own judgement. The contract is deliberately
unexpressive: FAST can never mutate the social world, propose memories, or
schedule tasks.
"""

import time
from collections.abc import Awaitable, Callable
from typing import Any

from len_bot.cognition.persona import (
    render_identity_block,
    render_register_block,
    render_self_state_block,
    render_voice_examples_block,
)
from len_bot.cognition.projection import (
    estimate_tokens,
    pack_recent_chat,
    project_onebot_text,
)
from len_bot.cognition.providers import ProviderRegistry
from len_bot.cognition.router import CognitiveTier
from len_bot.cognition.session import (
    FastCognitionResult,
    GroupAgentSession,
)
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, Stimulus

_SYSTEM_CONTRACT = (
    "【FAST CONTRACT】\n"
    "你是持续存在于这个群里的同一个社会成员。现在只做一次快速社会判断,没有工具、没有第二次思考。\n"
    "群聊大部分时候不需要你。SILENCE 是常态而非失败:没有自然插话位置、没被点名、接不上话、"
    "或者说了也只是客套,就输出 silence。\n"
    "要说话时,像一个真实群友那样短:反应、接梗、吐槽、简短追问、一句话站队,"
    "通常一行以内,最多两条。绝不要写解释、建议列表、安慰长文或任何客服式回复。\n"
    "只有当理解当前对话确实需要旧聊天记录、长期记忆、具体知识、工具,或者话题复杂需要完整推理时,"
    "才输出 full 交给完整认知处理——不要瞎编不知道的事。\n"
    "只输出一个 JSON 对象:\n"
    '{"decision": "silence|speak|full", "reason": "一句话理由", '
    '"messages": [{"content": "...", "reply_to": OneBotMessageID或null, '
    '"expect_reply": false, "reply_target": null, "reply_intent": null}]}\n'
    "messages 只在 decision=speak 时非空;reply_target 只有在期待某人回复时才填 user:QQ。"
)


class FastContextAssembler:
    def __init__(self, config: RuntimeConfig):
        self.config = config

    def assemble(
        self,
        session: GroupAgentSession,
        burst: Stimulus,
        raw_events: list[Event],
        active_open_loops: list[dict[str, Any]],
        voice_examples: list[dict] | None = None,
    ) -> list[dict[str, str]]:
        system_content = render_identity_block(self.config) + _SYSTEM_CONTRACT

        open_loops_text = (
            "\n".join(
                f"- id={loop['id']} target={loop.get('target_actor_id')} intent={loop.get('intent')}"
                for loop in active_open_loops
            )
            if active_open_loops
            else "(无)"
        )
        projected_burst = project_onebot_text(burst.combined_text)
        fixed_tail = (
            "【ACTIVE OPEN LOOPS】(你在等回应的事)\n"
            f"{open_loops_text}\n\n"
            "【CURRENT BURST】\n"
            f"{projected_burst}\n"
            f"SourceEventIDs: {burst.source_event_ids}\n"
            f"MentionBot: {burst.has_mention_bot} | ReplyBot: {burst.has_reply_bot}\n"
        )
        budget = max(
            0,
            self.config.fast_context_window_tokens
            - estimate_tokens(system_content)
            - estimate_tokens(fixed_tail)
            - estimate_tokens(render_self_state_block(session))
            - estimate_tokens(render_register_block(session))
            - estimate_tokens(render_voice_examples_block(voice_examples or []))
            - 200,  # completion reserve
        )
        recent_chat = pack_recent_chat(raw_events, budget, self.config.bot_qq)
        chat_text = "\n".join(recent_chat) if recent_chat else "(暂无近期原始对话)"

        user_content = (
            render_self_state_block(session)
            + render_register_block(session)
            + "【RECENT CHAT】\n"
            f"{chat_text}\n\n"
            + render_voice_examples_block(voice_examples or [])
            + fixed_tail
        )
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]


class FastSocialCognition:
    def __init__(
        self,
        config: RuntimeConfig,
        registry: ProviderRegistry,
        metrics: Any = None,
        mock_handler: Callable[[list[dict[str, str]]], Awaitable[FastCognitionResult]] | None = None,
    ):
        self.config = config
        self.registry = registry
        self.metrics = metrics
        self.mock_handler = mock_handler
        self.context_assembler = FastContextAssembler(config)

    async def execute(
        self,
        session: GroupAgentSession,
        burst: Stimulus,
        raw_events: list[Event],
        active_open_loops: list[dict[str, Any]],
        voice_examples: list[dict] | None = None,
        correction: str | None = None,
    ) -> tuple[FastCognitionResult, dict[str, Any]]:
        """One model round-trip; at most one corrective retry on a contract failure."""
        messages = self.context_assembler.assemble(
            session=session,
            burst=burst,
            raw_events=raw_events,
            active_open_loops=active_open_loops,
            voice_examples=voice_examples,
        )
        if correction:
            messages.append({"role": "user", "content": correction})

        if self.mock_handler:
            result = await self.mock_handler(messages)
            return FastCognitionResult.model_validate(result), {"mode": "mock", "path": "fast", "repairs": 0}

        trace: dict[str, Any] = {"mode": "live", "path": "fast", "repairs": 0}
        working_messages: list[dict[str, Any]] = list(messages)
        for attempt in range(2):
            response, resolution, used_fallback, latency = await self._call_model(working_messages)
            usage = getattr(response, "usage", None)
            if self.metrics:
                self.metrics.record_call(
                    CognitiveTier.FAST.value,
                    resolution.provider_id,
                    resolution.model,
                    latency,
                    prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                    completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                )
                if used_fallback:
                    self.metrics.inc_social("model_fallbacks")
            trace.update({
                "provider_id": resolution.provider_id,
                "model": resolution.model,
                "fallback": used_fallback,
                "latency_ms": round(latency * 1000),
                "prompt_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage, "completion_tokens", 0) or 0,
            })
            content = response.choices[0].message.content or ""
            try:
                return self._parse_result(content), trace
            except Exception as contract_error:
                if attempt == 1:
                    # Unrecoverable FAST contract failure escalates to FULL
                    # (fail-open to deeper cognition, never to a hallucinated reply).
                    trace["fast_contract_error"] = str(contract_error)[:200]
                    raise
                trace["repairs"] = 1
                working_messages.append({"role": "assistant", "content": content})
                working_messages.append({
                    "role": "user",
                    "content": (
                        f"上一份输出无法提交:{contract_error}。"
                        "重新只输出一个严格符合约定的 JSON 对象,不要输出其他文本。"
                    ),
                })
        raise RuntimeError("FAST cognition loop exited without decision")

    async def _call_model(self, messages: list[dict[str, Any]]) -> tuple[Any, Any, bool, float]:
        primary = self.registry.resolve(CognitiveTier.FAST)
        try:
            started = time.monotonic()
            response = await self._create_completion(primary, messages, self.config.fast_max_output_tokens)
            return response, primary, False, time.monotonic() - started
        except Exception as error:
            if self.metrics:
                self.metrics.record_error(CognitiveTier.FAST.value, primary.provider_id, primary.model, str(error))
            fallback = self.registry.resolve_fallback()
            if (
                fallback is None
                or (fallback.provider_id, fallback.model) == (primary.provider_id, primary.model)
            ):
                raise
            started = time.monotonic()
            try:
                response = await self._create_completion(fallback, messages, self.config.fast_max_output_tokens)
            except Exception as fallback_error:
                if self.metrics:
                    self.metrics.record_error(
                        CognitiveTier.FAST.value,
                        fallback.provider_id,
                        fallback.model,
                        str(fallback_error),
                    )
                raise
            return response, fallback, True, time.monotonic() - started

    @staticmethod
    async def _create_completion(resolution, messages, max_tokens: int):
        return await resolution.client.chat.completions.create(
            model=resolution.model,
            messages=messages,
            temperature=0.7,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )

    @staticmethod
    def _parse_result(text: str) -> FastCognitionResult:
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace < 0 or last_brace <= first_brace:
            raise ValueError("FAST cognition returned no JSON object")
        return FastCognitionResult.model_validate_json(text[first_brace:last_brace + 1])
