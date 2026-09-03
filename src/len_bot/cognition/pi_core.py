import json
import logging
from typing import Optional, Callable, Awaitable, Any
from openai import AsyncOpenAI
from len_bot.config import RuntimeConfig
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition, MessageProposal
from len_bot.cognition.mailbox import EpisodeMailbox, SteeringType
from len_bot.cognition.router import CognitionRouter, CognitiveTier

logger = logging.getLogger(__name__)

class PiAgentCore:
    def __init__(
        self,
        config: RuntimeConfig,
        mock_handler: Optional[Callable[[list[dict[str, str]]], Awaitable[EpisodeOutcome]]] = None
    ):
        self.config = config
        self.mock_handler = mock_handler
        self._client: Optional[AsyncOpenAI] = None
        if self.config.openai_api_key:
            self._client = AsyncOpenAI(
                api_key=self.config.openai_api_key,
                base_url=self.config.openai_base_url
            )

    async def execute_episode(
        self,
        messages: list[dict[str, str]],
        mailbox: EpisodeMailbox,
        toolkit: Optional[Any] = None,
        max_steps: int = 5
    ) -> EpisodeOutcome:
        # 1. Step-boundary Steering Check (ADR-0002)
        steering = mailbox.check_steering()
        if steering and steering.steering_type == SteeringType.CANCEL:
            logger.info("Episode %s aborted early by steering: %s", mailbox.episode_id, steering.reason)
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought=f"Aborted early by steering: {steering.reason}"
            )

        # 2. Mock handler for offline testing & benchmark scenarios
        if self.mock_handler is not None:
            import inspect
            sig = inspect.signature(self.mock_handler)
            if len(sig.parameters) >= 2:
                outcome = await self.mock_handler(messages, toolkit)
            else:
                outcome = await self.mock_handler(messages)

            # Re-check steering after cognition
            steering = mailbox.check_steering()
            if steering and steering.steering_type == SteeringType.CANCEL:
                return EpisodeOutcome(
                    disposition=FinalDisposition.SILENCE,
                    thought=f"Aborted by steering after mock cognition: {steering.reason}"
                )
            return outcome

        # 3. Live LLM Call via AsyncOpenAI Structured Output + ReAct Tool Loop
        if not self._client:
            logger.warning("No OpenAI API key provided. Falling back to default SILENCE.")
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought="No LLM API key configured"
            )

        try:
            working_messages = list(messages)
            tools = toolkit.get_tool_definitions() if toolkit else None
            router = CognitionRouter(self.config)
            current_tier = CognitiveTier.NORMAL

            # ReAct Step Loop
            for step in range(max_steps):
                steering = mailbox.check_steering()
                if steering:
                    if steering.steering_type == SteeringType.CANCEL:
                        return EpisodeOutcome(
                            disposition=FinalDisposition.SILENCE,
                            thought=f"Aborted by steering at step {step}: {steering.reason}"
                        )
                    elif steering.steering_type == SteeringType.FOLLOW_UP:
                        follow_up_content = f"【INTERIM FOLLOW-UP from {steering.source_event.actor_id}】: {steering.source_event.raw_text}"
                        working_messages.append({"role": "user", "content": follow_up_content})
                        logger.info("Injected follow-up into reasoning trajectory at step %d: %s", step, steering.source_event.raw_text)

                active_model = router.get_model_for_tier(current_tier)

                create_kwargs: dict[str, Any] = {
                    "model": active_model,
                    "messages": working_messages,
                    "temperature": 0.6,
                    "response_format": {"type": "json_object"}
                }
                if tools:
                    create_kwargs["tools"] = tools

                resp = await self._client.chat.completions.create(**create_kwargs)
                choice = resp.choices[0]
                msg = choice.message

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
                        # Check dynamic escalation (§75 & ADR-0012)
                        if router.should_escalate(current_tier, step, tool_result):
                            current_tier = CognitiveTier.DELIBERATE
                    continue

                # Single-pass structured output: parse final EpisodeOutcome directly
                raw_content = msg.content or ""
                outcome = self._parse_outcome(raw_content)

                # Check steering post-outcome
                steering = mailbox.check_steering()
                if steering and steering.steering_type == SteeringType.CANCEL:
                    return EpisodeOutcome(
                        disposition=FinalDisposition.SILENCE,
                        thought=f"Aborted by steering post-inference: {steering.reason}"
                    )
                return outcome

            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought="ReAct loop reached max steps without conclusion"
            )
        except Exception as e:
            logger.exception("PiAgentCore execution error: %s", e)
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought=f"Error in LLM inference: {e}"
            )

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
            thought=f"Model output violated structured contract (not valid EpisodeOutcome JSON): {text[:100]}"
        )
