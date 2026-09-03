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
    delay_seconds: float = Field(description="Seconds from now until task is due")
    payload: dict[str, Any] = Field(default_factory=dict)

from len_bot.memory.models import MemoryProposal

class EpisodeOutcome(BaseModel):
    disposition: FinalDisposition = Field(
        default=FinalDisposition.SILENCE,
        description="Must be SILENCE if no message should be sent, or ACTION if sending message(s)"
    )
    thought: str = Field(description="Brief structured chain of thought explaining the decision")
    message_proposals: list[MessageProposal] = Field(default_factory=list)
    task_proposals: list[TaskProposal] = Field(default_factory=list)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list)
    resolve_open_loop_ids: list[str] = Field(default_factory=list)
    state_annotations: dict[str, Any] = Field(default_factory=dict)
