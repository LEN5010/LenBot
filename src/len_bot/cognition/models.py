from enum import StrEnum
from typing import Optional, Any
from pydantic import BaseModel, Field

class FinalDisposition(StrEnum):
    SILENCE = "SILENCE"
    ACTION = "ACTION"

class MessageProposal(BaseModel):
    content: str = Field(description="The natural language message to send")
    reply_to: Optional[str] = Field(default=None, description="Event ID or message ID to quote-reply")
    expect_reply: bool = Field(default=False, description="Whether this message expects an answer from a specific user")
    reply_target: Optional[str] = Field(default=None, description="Actor ID expected to respond (e.g. user:123)")
    reply_intent: Optional[str] = Field(default=None, description="Topic or intent of expected answer")

class TaskProposal(BaseModel):
    description: str = Field(description="Goal or purpose of the scheduled future task")
    delay_seconds: Optional[float] = Field(
        default=None,
        description="Seconds from now until task is due. Omit for condition-bound tasks."
    )
    # ADR-0018: condition-bound obligation. When set, the task fires when a committed
    # event of this type arrives in the scene (e.g. LIVE_STARTED), or at its deadline.
    wake_event_type: Optional[str] = Field(default=None)
    wake_match: Optional[dict[str, Any]] = Field(default=None, description="Exact dict match against event payload (ADR-0029, §16)")
    payload: dict[str, Any] = Field(default_factory=dict)
    origin_episode_id: Optional[str] = Field(default=None)
    origin_stimulus_id: Optional[str] = Field(default=None)
    origin_mode: str = Field(default="live")


# Deadline cap for condition-bound tasks that never see their wake event (ADR-0018).
CONDITION_TASK_DEFAULT_DEADLINE_SECONDS = 604800.0


class RetainedItemProposal(BaseModel):
    """Optional cognition proposal to retain a short-lived ambient item (ADR-0018, Goal 7)."""
    topic: str = Field(description="Short topical label used for future lexical matching")
    summary: str = Field(description="What the agent saw / learned, in one or two sentences")
    salience: float = Field(default=0.5, description="Estimated usefulness if a related topic appears [0,1]")
    source_event_id: Optional[str] = Field(default=None, description="Optional evidence event id from recent raw chat")

class ThreadTransition(StrEnum):
    KEEP = "keep"
    FADE = "fade"
    CLOSE = "close"

class SocialStateProposal(BaseModel):
    topic: Optional[str] = Field(default=None, description="Current conversational topic if updated")
    thread_transition: Optional[ThreadTransition] = Field(
        default=None,
        description="Optional transition for participation thread: keep, fade, or close"
    )

from len_bot.memory.models import MemoryProposal

class EpisodeOutcome(BaseModel):
    disposition: FinalDisposition = Field(
        default=FinalDisposition.SILENCE,
        description="Must be SILENCE if no message should be sent, or ACTION if sending message(s)"
    )
    decision_reason: str = Field(description="Brief structured reason explaining the decision (e.g. peer already answered)")
    message_proposals: list[MessageProposal] = Field(default_factory=list)
    task_proposals: list[TaskProposal] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)
    resolve_open_loop_ids: list[str] = Field(default_factory=list)
    social_state_proposal: Optional[SocialStateProposal] = Field(default=None)
    retained_item_proposals: list[RetainedItemProposal] = Field(default_factory=list)
