import asyncio
import time
from typing import Optional, Callable, Awaitable
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.runtime.agent_runtime import AgentRuntime
from len_bot.actions.models import ActionItem
from len_bot.cognition.session import SocialCognitionResult, FastCognitionResult

class ScenarioRunner:
    def __init__(
        self,
        config: RuntimeConfig,
        mock_social_handler: Optional[Callable[[list[dict[str, str]]], Awaitable[SocialCognitionResult]]] = None,
        mock_fast_handler: Optional[Callable[[list[dict[str, str]]], Awaitable[FastCognitionResult]]] = None,
    ):
        self.config = config
        self.sent_actions: list[ActionItem] = []
        self.runtime = AgentRuntime(
            config=config,
            send_adapter=self._mock_send_adapter,
            mock_social_handler=mock_social_handler,
            mock_fast_handler=mock_fast_handler,
        )

    async def _mock_send_adapter(self, action: ActionItem) -> bool:
        self.sent_actions.append(action)
        return True

    async def setup(self) -> None:
        await self.runtime.start()

    async def teardown(self) -> None:
        await self.runtime.stop()

    async def step_message(
        self,
        scene_id: str,
        user_id: int,
        text: str,
        at_bot: bool = False,
        reply_bot: bool = False,
        timestamp: Optional[float] = None
    ) -> Event:
        timestamp_val = timestamp if timestamp is not None else time.time()
        event = Event(
            event_type=EventType.GROUP_MESSAGE_RECEIVED,
            scene_id=scene_id,
            actor_id=f"user:{user_id}",
            timestamp=timestamp_val,
            payload={
                "raw_text": text,
                "at_bot": at_bot,
                "reply_bot": reply_bot
            }
        )
        await self.runtime.receive_event(event)
        return event

    async def settle(self, wait_seconds: float = 1.0) -> None:
        """Allow async debounce windows and background workers to settle."""
        await asyncio.sleep(wait_seconds)
