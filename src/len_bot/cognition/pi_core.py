import json
import logging
from typing import Optional, Callable, Awaitable
from openai import AsyncOpenAI
from len_bot.config import RuntimeConfig
from len_bot.cognition.models import EpisodeOutcome, FinalDisposition
from len_bot.cognition.mailbox import EpisodeMailbox, SteeringType

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
        mailbox: EpisodeMailbox
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
            outcome = await self.mock_handler(messages)
            # Re-check steering after cognition
            steering = mailbox.check_steering()
            if steering and steering.steering_type == SteeringType.CANCEL:
                return EpisodeOutcome(
                    disposition=FinalDisposition.SILENCE,
                    thought=f"Aborted by steering after mock cognition: {steering.reason}"
                )
            return outcome

        # 3. Live LLM Call via AsyncOpenAI Structured Output
        if not self._client:
            logger.warning("No OpenAI API key provided. Falling back to default SILENCE.")
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought="No LLM API key configured"
            )

        try:
            # Use structured outputs parsing EpisodeOutcome
            completion = await self._client.beta.chat.completions.parse(
                model=self.config.default_model,
                messages=messages,
                response_format=EpisodeOutcome,
                temperature=0.6,
            )
            outcome = completion.choices[0].message.parsed
            if outcome is None:
                raise ValueError("Model failed to produce parsed EpisodeOutcome")

            # Check steering at step boundary
            steering = mailbox.check_steering()
            if steering and steering.steering_type == SteeringType.CANCEL:
                return EpisodeOutcome(
                    disposition=FinalDisposition.SILENCE,
                    thought=f"Aborted by steering post-inference: {steering.reason}"
                )
            return outcome
        except Exception as e:
            logger.exception("PiAgentCore execution error: %s", e)
            return EpisodeOutcome(
                disposition=FinalDisposition.SILENCE,
                thought=f"Error in LLM inference: {e}"
            )
