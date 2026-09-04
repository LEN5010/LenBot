from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryCertainty, MemoryKind


class SessionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SocialThreadStatus(StrEnum):
    ACTIVE = "active"
    WAITING = "waiting"
    RESOLVED = "resolved"


class TopicState(SessionModel):
    id: str
    subject: str
    status: str = "active"
    participants: list[str] = Field(default_factory=list)
    context: str = ""


class SocialThreadState(SessionModel):
    id: str
    summary: str
    status: SocialThreadStatus = SocialThreadStatus.ACTIVE
    participants: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)


class LatentExpectation(SessionModel):
    id: str
    condition: str
    context: str
    source_event_ids: list[str]


class SocialWorldState(SessionModel):
    mood: str = "unknown"
    activity: str = "unknown"
    topics: list[TopicState] = Field(default_factory=list)
    social_dynamics: list[str] = Field(default_factory=list)
    open_threads: list[SocialThreadState] = Field(default_factory=list)
    latent_expectations: list[LatentExpectation] = Field(default_factory=list)


class SelfSocialState(SessionModel):
    engagement: str = "observing"
    social_position: str = "observer"
    current_interest: str = "unknown"
    inclination_to_speak: str = "unknown"
    last_bot_message_event_id: str | None = None
    last_bot_message_at: float | None = None
    consecutive_bot_messages: int = 0
    human_messages_since_bot: int = 0
    recent_feedback: list[str] = Field(default_factory=list)


class GroupIdentity(SessionModel):
    familiarity: str = "unknown"
    common_topics: list[str] = Field(default_factory=list)
    internal_expressions: list[str] = Field(default_factory=list)
    usual_role: str = "observer"
    norms: list[str] = Field(default_factory=list)


class WorkingPersonModel(SessionModel):
    actor_id: str
    display_name: str | None = None
    group_role: str | None = None
    recent_context: list[str] = Field(default_factory=list)
    recent_event_ids: list[str] = Field(default_factory=list)


class RelationshipModel(SessionModel):
    actor_id: str
    familiarity: str = "unknown"
    patterns: list[str] = Field(default_factory=list)
    recent_interaction_event_ids: list[str] = Field(default_factory=list)


class RetainedAttentionItem(SessionModel):
    id: str
    summary: str
    source_event_ids: list[str]
    expires_at: float | None = None


class NextWakeIntent(SessionModel):
    reason: str
    wake_at: float
    source_event_ids: list[str]


class SocialDecisionAction(StrEnum):
    SILENCE = "silence"
    SPEAK = "speak"


class SelfSocialStateUpdate(SessionModel):
    engagement: str
    social_position: str
    current_interest: str
    inclination_to_speak: str
    recent_feedback: list[str] = Field(default_factory=list)


class WorkingPersonUpdate(SessionModel):
    actor_id: str
    recent_context: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)


class RelationshipUpdate(SessionModel):
    actor_id: str
    familiarity: str
    patterns: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)


class SocialPerception(SessionModel):
    summary: str
    world_state: SocialWorldState
    person_updates: list[WorkingPersonUpdate] = Field(default_factory=list)
    relationship_updates: list[RelationshipUpdate] = Field(default_factory=list)


class SocialDecision(SessionModel):
    action: SocialDecisionAction
    reason: str


class SocialMessageProposal(SessionModel):
    content: str
    reply_to: str | None = None
    expect_reply: bool = False
    reply_target: str | None = None
    reply_intent: str | None = None


class SocialTaskProposal(SessionModel):
    description: str
    delay_seconds: float | None = None
    wake_event_type: str | None = None
    wake_match: dict[str, object] | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class SocialMemoryCandidate(SessionModel):
    subject: str
    kind: MemoryKind
    key: str
    value: str
    temporal: str = "recent"
    certainty: MemoryCertainty = MemoryCertainty.LIKELY
    evidence: list[str]
    human_readable_assertion: str


class RetainedAttentionProposal(SessionModel):
    summary: str
    source_event_ids: list[str]
    expires_at: float | None = None


class NextWakeIntentProposal(SessionModel):
    reason: str
    wake_at: float
    source_event_ids: list[str]


class SocialCognitionResult(SessionModel):
    perception: SocialPerception
    self_state: SelfSocialStateUpdate
    decision: SocialDecision
    message_proposals: list[SocialMessageProposal] = Field(default_factory=list)
    task_proposals: list[SocialTaskProposal] = Field(default_factory=list)
    resolve_open_loop_ids: list[str] = Field(default_factory=list)
    retained_attention: list[RetainedAttentionProposal] = Field(default_factory=list)
    future_attention: NextWakeIntentProposal | None = None
    memory_candidates: list[SocialMemoryCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_contract(self) -> "SocialCognitionResult":
        if self.decision.action == SocialDecisionAction.SPEAK and not self.message_proposals:
            raise ValueError("speak requires at least one message proposal")
        if self.decision.action == SocialDecisionAction.SILENCE and self.message_proposals:
            raise ValueError("silence cannot contain message proposals")
        return self

    def to_episode_outcome(self, scene_id: str):
        from len_bot.cognition.models import (
            EpisodeOutcome,
            FinalDisposition,
            MessageProposal,
            TaskProposal,
        )
        from len_bot.memory.models import MemoryProposal

        disposition = (
            FinalDisposition.ACTION
            if self.decision.action == SocialDecisionAction.SPEAK
            else FinalDisposition.SILENCE
        )
        return EpisodeOutcome(
            disposition=disposition,
            decision_reason=self.decision.reason,
            message_proposals=[
                MessageProposal(**proposal.model_dump())
                for proposal in self.message_proposals
            ],
            task_proposals=[
                TaskProposal(**proposal.model_dump())
                for proposal in self.task_proposals
            ],
            memory_proposals=[
                MemoryProposal(scope=scene_id, **candidate.model_dump())
                for candidate in self.memory_candidates
            ],
            resolve_open_loop_ids=self.resolve_open_loop_ids,
        )


class GroupAgentSession(SessionModel):
    scene_id: str
    version: int = 0
    last_observed_event_rowid: int = 0
    last_cognized_event_rowid: int = 0
    conversation_event_ids: list[str] = Field(default_factory=list)
    social_world: SocialWorldState = Field(default_factory=SocialWorldState)
    self_social_state: SelfSocialState = Field(default_factory=SelfSocialState)
    group_identity: GroupIdentity = Field(default_factory=GroupIdentity)
    working_persons: dict[str, WorkingPersonModel] = Field(default_factory=dict)
    working_relationships: dict[str, RelationshipModel] = Field(default_factory=dict)
    retained_attention: list[RetainedAttentionItem] = Field(default_factory=list)
    next_wake_intent: NextWakeIntent | None = None
    recent_episode_summary: str | None = None


class GroupAgentSessionReducer:
    _CONVERSATION_EVENT_TYPES = {
        EventType.GROUP_MESSAGE_RECEIVED,
        EventType.PRIVATE_MESSAGE_RECEIVED,
        EventType.MESSAGE_SENT,
        EventType.MESSAGE_SEND_FAILED,
        EventType.TASK_DUE,
        EventType.LIVE_STARTED,
        EventType.LIVE_ENDED,
    }
    _HUMAN_MESSAGE_TYPES = {
        EventType.GROUP_MESSAGE_RECEIVED,
        EventType.PRIVATE_MESSAGE_RECEIVED,
    }

    @classmethod
    def reduce(
        cls,
        state: GroupAgentSession,
        event: Event,
        bot_actor_id: str,
    ) -> GroupAgentSession:
        """Advance only factual working state; social meaning is cognition-owned."""
        candidate = state.model_copy(deep=True)
        candidate.version += 1

        if event.event_type in cls._CONVERSATION_EVENT_TYPES:
            candidate.conversation_event_ids.append(event.id)

        if event.event_type in cls._HUMAN_MESSAGE_TYPES and event.actor_id != bot_actor_id:
            candidate.self_social_state.consecutive_bot_messages = 0
            candidate.self_social_state.human_messages_since_bot += 1
            cls._record_person(candidate, event)
        elif event.event_type == EventType.MESSAGE_SENT and event.actor_id == bot_actor_id:
            self_state = candidate.self_social_state
            self_state.last_bot_message_event_id = event.id
            self_state.last_bot_message_at = event.timestamp
            self_state.consecutive_bot_messages += 1
            self_state.human_messages_since_bot = 0

        return candidate

    @staticmethod
    def _record_person(state: GroupAgentSession, event: Event) -> None:
        sender = event.payload.get("sender") or {}
        person = state.working_persons.get(event.actor_id)
        if person is None:
            person = WorkingPersonModel(actor_id=event.actor_id)

        display_name = sender.get("card") or sender.get("nickname")
        if display_name:
            person.display_name = str(display_name)
        if sender.get("role"):
            person.group_role = str(sender["role"])
        person.recent_event_ids.append(event.id)
        state.working_persons[event.actor_id] = person

    @classmethod
    def apply_cognition(
        cls,
        state: GroupAgentSession,
        result: SocialCognitionResult,
        through_event_rowid: int,
    ) -> GroupAgentSession:
        candidate = state.model_copy(deep=True)
        candidate.version += 1
        candidate.last_cognized_event_rowid = through_event_rowid
        candidate.social_world = result.perception.world_state

        update = result.self_state
        self_state = candidate.self_social_state
        self_state.engagement = update.engagement
        self_state.social_position = update.social_position
        self_state.current_interest = update.current_interest
        self_state.inclination_to_speak = update.inclination_to_speak
        self_state.recent_feedback = update.recent_feedback

        for person_update in result.perception.person_updates:
            person = candidate.working_persons.get(person_update.actor_id)
            if person is None:
                person = WorkingPersonModel(actor_id=person_update.actor_id)
            person.recent_context = person_update.recent_context
            person.recent_event_ids = person_update.source_event_ids
            candidate.working_persons[person_update.actor_id] = person

        for relationship_update in result.perception.relationship_updates:
            candidate.working_relationships[relationship_update.actor_id] = RelationshipModel(
                actor_id=relationship_update.actor_id,
                familiarity=relationship_update.familiarity,
                patterns=relationship_update.patterns,
                recent_interaction_event_ids=relationship_update.source_event_ids,
            )

        candidate.retained_attention.extend(
            RetainedAttentionItem(
                id=f"retained:{through_event_rowid}:{index}",
                summary=item.summary,
                source_event_ids=item.source_event_ids,
                expires_at=item.expires_at,
            )
            for index, item in enumerate(result.retained_attention)
        )
        if result.future_attention:
            candidate.next_wake_intent = NextWakeIntent(
                **result.future_attention.model_dump()
            )
        return candidate
