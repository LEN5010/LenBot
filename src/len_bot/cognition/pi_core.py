import json
import logging
import time
from typing import Optional, Callable, Awaitable, Any
from len_bot.config import RuntimeConfig
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.mailbox import EpisodeMailbox, SteeringType
from len_bot.cognition.providers import ProviderConfig, ProviderRegistry, RouteTarget, RoutingConfig
from len_bot.cognition.router import CognitionRouter, CognitiveTier

logger = logging.getLogger(__name__)

class PiAgentCore:
    def __init__(
        self,
        config: RuntimeConfig,
        registry: Optional[ProviderRegistry] = None,
        metrics: Optional[Any] = None,
        mock_handler: Optional[Callable[[list[dict[str, str]]], Awaitable[EpisodeOutcome]]] = None
    ):
        self.config = config
        self.mock_handler = mock_handler
        # ADR-0020: the registry is the single model/client authority. When none is
        # injected (direct construction in tests), seed one from RuntimeConfig —
        # safe synchronously because the object is not yet shared with any loop.
        self.registry = registry or ProviderRegistry()
        if registry is None:
            seed_provider = ProviderConfig(
                id="default",
                base_url=config.openai_base_url,
                api_key=config.openai_api_key,
                models=list(dict.fromkeys([config.default_model, config.deliberate_model])),
            )
            seed_routing = RoutingConfig(
                normal=RouteTarget(provider_id="default", model=config.default_model),
                deliberate=RouteTarget(provider_id="default", model=config.deliberate_model),
            )
            self.registry._providers = {seed_provider.id: seed_provider}
            self.registry._routing = seed_routing
        self.metrics = metrics

    async def execute_episode(
        self,
        messages: list[dict[str, str]],
        mailbox: EpisodeMailbox,
        toolkit: Optional[Any] = None,
        max_steps: int = 5
    ) -> tuple[EpisodeOutcome, dict[str, Any]]:
        """Runs one cognitive episode. Returns (outcome, step_trace) — the trace
        carries the per-step model/tier/tool chain for the Control Plane (ADR-0022)."""
        trace: dict[str, Any] = {"mode": "live", "steps": [], "interim_injections": 0, "follow_ups": 0, "escalations": []}

        # 1. Early cancellation check before starting ReAct loop
        if mailbox.is_cancelled():
            reason = mailbox.cancellation_reason() or "Episode cancelled by steering"
            logger.info("Episode %s aborted early by steering: %s", mailbox.episode_id, reason)
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                decision_reason=f"Aborted early by steering: {reason}"
            ), trace

        # 2. Mock handler for offline testing & benchmark scenarios
        if self.mock_handler is not None:
            trace["mode"] = "mock"
            import inspect
            sig = inspect.signature(self.mock_handler)
            working_messages = list(messages)
            if len(sig.parameters) >= 2:
                outcome = await self.mock_handler(working_messages, toolkit)
            else:
                outcome = await self.mock_handler(working_messages)

            if mailbox.is_cancelled():
                return EpisodeOutcome(
                    disposition=FinalDisposition.SILENCE,
                    decision_reason=f"Aborted by steering after mock cognition: {mailbox.cancellation_reason()}"
                ), trace

            # Check for follow-ups or interim events arrived during cognition
            has_fu = mailbox.has_follow_up()
            unseen = mailbox.fetch_unseen_interim_events()
            if has_fu or unseen:
                updated_messages = list(working_messages)
                if has_fu:
                    follow_ups = mailbox.consume_follow_ups()
                    trace["follow_ups"] += len(follow_ups)
                    for fu in follow_ups:
                        updated_messages.append({
                            "role": "user",
                            "content": f"【INTERIM FOLLOW-UP from {fu.actor_id}】: {fu.raw_text}"
                        })
                if unseen:
                    interim_texts = [
                        f"[{e.actor_id}]: {e.raw_text}"
                        for e in unseen if e.raw_text and not e.is_mention_bot and not e.is_reply_bot
                    ]
                    if interim_texts:
                        trace["interim_injections"] += 1
                        updated_messages.append({
                            "role": "user",
                            "content": "【INTERIM SCENE ACTIVITY】\n" + "\n".join(interim_texts) + "\n(Note: If the conversation indicates the query was answered or resolved by others, choose FinalDisposition.SILENCE)"
                        })

                if len(sig.parameters) >= 2:
                    outcome = await self.mock_handler(updated_messages, toolkit)
                else:
                    outcome = await self.mock_handler(updated_messages)
            return outcome, trace

        # 3. Live LLM Call via AsyncOpenAI Structured Output + ReAct Tool Loop
        try:
            working_messages = list(messages)
            tools = toolkit.get_tool_definitions() if toolkit else None
            router = CognitionRouter()
            current_tier = CognitiveTier.NORMAL

            # ReAct Step Loop
            for step in range(max_steps):
                if mailbox.is_cancelled():
                    return EpisodeOutcome(
                        disposition=FinalDisposition.SILENCE,
                        decision_reason=f"Aborted by steering at step {step}: {mailbox.cancellation_reason()}"
                    ), trace

                # ADR-0020: resolve client+model per step from the registry — tier
                # switches and hot config updates take effect at the next step.
                try:
                    resolution = self.registry.resolve(current_tier)
                except LookupError as e:
                    logger.warning("No provider resolvable for tier %s: %s", current_tier, e)
                    return EpisodeOutcome(
                        disposition=FinalDisposition.SILENCE,
                        decision_reason=f"No LLM provider configured: {e}"
                    ), trace

                if mailbox.has_follow_up():
                    follow_ups = mailbox.consume_follow_ups()
                    trace["follow_ups"] += len(follow_ups)
                    for fu in follow_ups:
                        working_messages.append({
                            "role": "user",
                            "content": f"【INTERIM FOLLOW-UP from {fu.actor_id}】: {fu.raw_text}"
                        })
                        logger.info("Injected follow-up into reasoning trajectory at step %d: %s", step, fu.raw_text)

                # Inject unseen interim scene events (Goal 8 & Section 8.2)
                unseen = mailbox.fetch_unseen_interim_events()
                interim_texts = [
                    f"[{e.actor_id}]: {e.raw_text}"
                    for e in unseen if e.raw_text and not e.is_mention_bot and not e.is_reply_bot
                ]
                if interim_texts:
                    trace["interim_injections"] += 1
                    working_messages.append({
                        "role": "user",
                        "content": "【INTERIM SCENE ACTIVITY】\n" + "\n".join(interim_texts) + "\n(Note: If the conversation indicates the query was answered or resolved by others, choose FinalDisposition.SILENCE)"
                    })
                    logger.info("Injected interim context into reasoning trajectory at step %d (%d events)", step, len(interim_texts))

                active_model = resolution.model

                create_kwargs: dict[str, Any] = {
                    "model": active_model,
                    "messages": working_messages,
                    "temperature": 0.6,
                    "response_format": {"type": "json_object"}
                }
                if tools:
                    create_kwargs["tools"] = tools

                # ADR-0020 routing metrics: latency/tokens/success per (tier, provider, model)
                call_started = time.monotonic()
                try:
                    resp = await resolution.client.chat.completions.create(**create_kwargs)
                except Exception as llm_error:
                    if self.metrics:
                        self.metrics.record_error(
                            current_tier.value, resolution.provider_id, active_model, str(llm_error)
                        )
                    raise
                latency = time.monotonic() - call_started
                usage = getattr(resp, "usage", None)
                if self.metrics:
                    self.metrics.record_call(
                        current_tier.value,
                        resolution.provider_id,
                        active_model,
                        latency,
                        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
                        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
                    )
                choice = resp.choices[0]
                msg = choice.message
                step_entry: dict[str, Any] = {
                    "step": step,
                    "tier": current_tier.value,
                    "provider_id": resolution.provider_id,
                    "model": active_model,
                    "tool_calls": [],
                }
                trace["steps"].append(step_entry)

                # If model issued tool calls, execute them and continue ReAct loop
                if getattr(msg, "tool_calls", None):
                    working_messages.append(msg.model_dump())
                    for tc in msg.tool_calls:
                        fn_name = tc.function.name
                        fn_args = json.loads(tc.function.arguments)
                        tool_result = await toolkit.execute(fn_name, fn_args)
                        working_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_result
                        })
                        step_entry["tool_calls"].append({
                            "name": fn_name,
                            "arguments": fn_args,
                            "result_preview": tool_result[:200]
                        })
                        # Check dynamic escalation (§75 & ADR-0012 & ADR-0020)
                        escalation_reason = router.should_escalate(current_tier, step, tool_result)
                        if escalation_reason:
                            current_tier = CognitiveTier.DELIBERATE
                            trace["escalations"].append({"step": step, "reason": escalation_reason})
                            if self.metrics:
                                self.metrics.record_escalation(escalation_reason)
                    continue

                # Check if cancellation arrived during this step's LLM generation
                if mailbox.is_cancelled():
                    return EpisodeOutcome(
                        disposition=FinalDisposition.SILENCE,
                        decision_reason=f"Aborted by steering post-inference: {mailbox.cancellation_reason()}"
                    ), trace

                # Check if follow-up arrived during this step's LLM generation
                if mailbox.has_follow_up():
                    follow_ups = mailbox.consume_follow_ups()
                    trace["follow_ups"] += len(follow_ups)
                    if self.metrics:
                        self.metrics.inc_social("followups_incorporated", len(follow_ups))
                    if step < max_steps - 1:
                        for fu in follow_ups:
                            working_messages.append({
                                "role": "user",
                                "content": f"【INTERIM FOLLOW-UP from {fu.actor_id}】: {fu.raw_text}"
                            })
                            logger.info("Injected late follow-up into reasoning trajectory at step %d: %s", step, fu.raw_text)
                        continue
                    else:
                        logger.warning("Follow-up arrived at max steps; discarding partial outcome to avoid answering stale context")
                        return EpisodeOutcome(
                            disposition=FinalDisposition.SILENCE,
                            decision_reason="Follow-up arrived at max steps; discarded to prevent answering stale context"
                        ), trace

                # Check if new interim events arrived during this step's generation (ADR-0026, §8.1)
                if mailbox.has_unseen_interim():
                    if step < max_steps - 1:
                        late_unseen = mailbox.fetch_unseen_interim_events()
                        late_interim = [
                            f"[{e.actor_id}]: {e.raw_text}"
                            for e in late_unseen if e.raw_text and not e.is_mention_bot and not e.is_reply_bot
                        ]
                        if late_interim:
                            trace["interim_injections"] += 1
                            working_messages.append({
                                "role": "user",
                                "content": "【INTERIM SCENE ACTIVITY】\n" + "\n".join(late_interim) + "\n(Note: If the conversation indicates the query was answered or resolved by others, choose FinalDisposition.SILENCE)"
                            })
                            logger.info("Injected late interim context into reasoning trajectory at step %d (%d events)", step, len(late_interim))
                            continue
                    else:
                        logger.warning("Interim events arrived at max steps; failing closed to SILENCE to avoid stale response")
                        return EpisodeOutcome(
                            disposition=FinalDisposition.SILENCE,
                            decision_reason="Interim events arrived at max steps; discarded to prevent answering stale context"
                        ), trace

                # No pending follow-ups: parse and return final structured outcome
                raw_content = msg.content or ""
                outcome = self._parse_outcome(raw_content)
                return outcome, trace

            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                decision_reason="ReAct loop reached max steps without conclusion"
            ), trace
        except Exception as e:
            logger.exception("PiAgentCore execution error: %s", e)
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                decision_reason=f"Error in LLM inference: {e}"
            ), trace

    def _parse_outcome(self, text: str) -> EpisodeOutcome:
        cleaned = text.strip()
        first_brace = cleaned.find("{")
        last_brace = cleaned.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidate = cleaned[first_brace:last_brace + 1]
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return EpisodeOutcome.model_validate(data)
            except Exception as e:
                logger.warning("Failed parsing EpisodeOutcome from JSON: %s (candidate: %s)", e, candidate[:200])

        # Strict contract enforcement (Item 5): reject non-structured output. Zero fail-open.
        logger.warning("Rejecting invalid non-structured model output (contract violation): %s", text[:200])
        return EpisodeOutcome(
            disposition=FinalDisposition.SILENCE,
            decision_reason=f"Model output violated structured contract (not valid EpisodeOutcome JSON): {text[:100]}"
        )
