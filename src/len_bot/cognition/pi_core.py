import json
import logging
from typing import Optional, Callable, Awaitable, Any
from openai import AsyncOpenAI
from len_bot.config import RuntimeConfig
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
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
                if steering and steering.steering_type == SteeringType.CANCEL:
                    return EpisodeOutcome(
                        disposition=FinalDisposition.SILENCE,
                        thought=f"Aborted by steering at step {step}: {steering.reason}"
                    )

                active_model = router.get_model_for_tier(current_tier)

                if tools:
                    resp = await self._client.chat.completions.create(
                        model=active_model,
                        messages=working_messages,
                        tools=tools,
                        temperature=0.6
                    )
                    choice = resp.choices[0]
                    msg = choice.message
                    if msg.tool_calls:
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

                # No tool calls needed or tools completed: parse final structured EpisodeOutcome
                active_model = router.get_model_for_tier(current_tier)
                completion = await self._client.beta.chat.completions.parse(
                    model=active_model,
                    messages=working_messages,
                    response_format=EpisodeOutcome,
                    temperature=0.6,
                )
                outcome = completion.choices[0].message.parsed
                if outcome is None:
                    raise ValueError("Model failed to produce parsed EpisodeOutcome")

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
