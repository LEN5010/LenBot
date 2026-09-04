from __future__ import annotations

import time
from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.cognition.projection import project_onebot_text
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


class GroupRegisterState(SessionModel):
    """ADR-0038: per-scene factual style statistics over human messages.

    Maintained by the deterministic reducer; rendered as style CONTEXT for the
    model. Nothing in the runtime makes decisions from these numbers.
    """

    MAX_WINDOW: ClassVar[int] = 100
    MAX_REACTION_KEYS: ClassVar[int] = 32

    sample_size: int = 0
    recent_lengths: list[int] = Field(default_factory=list)
    fragment_count: int = 0
    punctuated_count: int = 0
    emoji_count: int = 0
    question_count: int = 0
    short_reactions: dict[str, int] = Field(default_factory=dict)

    @classmethod
    def observe(cls, register: "GroupRegisterState", text: str) -> None:
        stripped = text.strip()
        if not stripped:
            return
        projected = project_onebot_text(stripped)
        register.sample_size += 1
        register.recent_lengths.append(len(projected))
        if len(register.recent_lengths) > register.MAX_WINDOW:
            register.recent_lengths = register.recent_lengths[-register.MAX_WINDOW:]
        if len(projected) <= 6:
            register.fragment_count += 1
        if projected[-1:] in "。？！?!.~,，、…":
            register.punctuated_count += 1
        if any("\U0001F300" <= ch <= "\U0001FAFF" or ch in "😊😂🤣😅😍🤔😭👍🔥" for ch in projected) or "[表情]" in projected:
            register.emoji_count += 1
        if "？" in projected or "?" in projected:
            register.question_count += 1
        if 0 < len(stripped) <= 4:
            register.short_reactions[stripped] = register.short_reactions.get(stripped, 0) + 1
            if len(register.short_reactions) > register.MAX_REACTION_KEYS:
                # Evict the least-common entry to bound state size.
                rarest = min(register.short_reactions, key=register.short_reactions.get)
                register.short_reactions.pop(rarest, None)

    def render(self) -> str:
        if self.sample_size == 0:
            return "(暂无统计)"
        lengths = sorted(self.recent_lengths)
        median = lengths[len(lengths) // 2] if lengths else 0
        n = self.sample_size
        top = sorted(self.short_reactions.items(), key=lambda kv: -kv[1])[:8]
        reactions = "、".join(f"“{key}”×{count}" for key, count in top) or "无"
        return (
            f"样本 {n} 条 | 消息中位长度 {median} 字 | "
            f"碎片化(≤6字) {self.fragment_count / n:.0%} | 带标点 {self.punctuated_count / n:.0%} | "
            f"带表情 {self.emoji_count / n:.0%} | 问句 {self.question_count / n:.0%} | "
            f"常见短反应: {reactions}"
        )


class GroupIdentityPatch(SessionModel):
    familiarity: str | None = None
    common_topics_add: list[str] = Field(default_factory=list)
    internal_expressions_add: list[str] = Field(default_factory=list)
    norms_add: list[str] = Field(default_factory=list)


class SocialWorldPatch(SessionModel):
    """ADR-0038: deferred, merge-only social-world proposal from quiet-window reflection.

    Merge-only semantics keep it causally safe against a fresher FULL snapshot:
    it can add or close topics and append dynamics, never wholesale replace.
    """

    mood: str | None = None
    activity: str | None = None
    open_topics: list[TopicState] = Field(default_factory=list)
    close_topic_ids: list[str] = Field(default_factory=list)
    social_dynamics_add: list[str] = Field(default_factory=list)
    group_identity: GroupIdentityPatch | None = None
    source_event_ids: list[str] = Field(default_factory=list)


class SocialDecisionAction(StrEnum):
    SILENCE = "silence"
    SPEAK = "speak"


class FastDecisionAction(StrEnum):
    """ADR-0038: FAST one-shot outcome — silence, speak-final, or escalate to FULL."""

    SILENCE = "silence"
    SPEAK = "speak"
    FULL = "full"


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
    reply_to: str | None = Field(
        default=None,
        description="OneBot message_id to quote; never use a LenBot Event ID",
    )
    expect_reply: bool = False
    reply_target: str | None = None
    reply_intent: str | None = None

    @field_validator("reply_to", mode="before")
    @classmethod
    def normalize_onebot_message_id(cls, value):
        return str(value) if value is not None else None


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
        if any(proposal.payload.get("kind") == "next_wake" for proposal in self.task_proposals):
            raise ValueError("next_wake is reserved for future_attention")
        return self

    def to_episode_outcome(self, scene_id: str, through_event_rowid: int):
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
        task_proposals = [
            TaskProposal(**proposal.model_dump())
            for proposal in self.task_proposals
        ]
        if self.future_attention is not None:
            # ADR-0034: a next-wake intent becomes a durable task on the exact
            # TaskProposal authority path; the task payload records the observed
            # cursor at creation so the runtime can reject evidence-free renewal.
            task_proposals.append(
                TaskProposal(
                    description=f"[next wake] {self.future_attention.reason}",
                    delay_seconds=max(0.0, self.future_attention.wake_at - time.time()),
                    payload={
                        "kind": "next_wake",
                        "reason": self.future_attention.reason,
                        "observed_event_rowid": through_event_rowid,
                        "source_event_ids": self.future_attention.source_event_ids,
                    },
                )
            )
        return EpisodeOutcome(
            disposition=disposition,
            decision_reason=self.decision.reason,
            message_proposals=[
                MessageProposal(**proposal.model_dump())
                for proposal in self.message_proposals
            ],
            task_proposals=task_proposals,
            memory_proposals=[
                MemoryProposal(scope=scene_id, **candidate.model_dump())
                for candidate in self.memory_candidates
            ],
            resolve_open_loop_ids=self.resolve_open_loop_ids,
        )


class FastCognitionResult(SessionModel):
    """ADR-0038: the one-shot FAST contract.

    Deliberately unexpressive: no world/person/relationship updates, no memory
    candidates, no task proposals — those belong to FULL cognition and to the
    deferred reflection channel.
    """

    decision: FastDecisionAction
    reason: str
    messages: list[SocialMessageProposal] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_contract(self) -> "FastCognitionResult":
        if self.decision == FastDecisionAction.SPEAK and not self.messages:
            raise ValueError("fast speak requires at least one message proposal")
        if self.decision in (FastDecisionAction.SILENCE, FastDecisionAction.FULL) and self.messages:
            raise ValueError("fast silence/full cannot contain message proposals")
        return self

    def to_episode_outcome(self, scene_id: str, through_event_rowid: int):
        from len_bot.cognition.models import (
            EpisodeOutcome,
            FinalDisposition,
            MessageProposal,
        )

        disposition = (
            FinalDisposition.ACTION
            if self.decision == FastDecisionAction.SPEAK
            else FinalDisposition.SILENCE
        )
        return EpisodeOutcome(
            disposition=disposition,
            decision_reason=f"[fast] {self.reason}",
            message_proposals=[
                MessageProposal(**proposal.model_dump()) for proposal in self.messages
            ],
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
    group_register: GroupRegisterState = Field(default_factory=GroupRegisterState)
    working_persons: dict[str, WorkingPersonModel] = Field(default_factory=dict)
    working_relationships: dict[str, RelationshipModel] = Field(default_factory=dict)
    retained_attention: list[RetainedAttentionItem] = Field(default_factory=list)
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
        cls._prune_expired_retained(candidate, event.timestamp)

        # ADR-0038: deferred merge-only social-world patches ride the standard
        # event path so the SceneActor stays the single session writer.
        if event.event_type == EventType.SOCIAL_COGNITION_RECORDED and event.payload.get("kind") == "deferred_patch":
            try:
                patch = SocialWorldPatch.model_validate(event.payload["patch"])
            except Exception:
                return candidate
            return cls.apply_deferred_patch(candidate, patch)

        if event.event_type in cls._CONVERSATION_EVENT_TYPES:
            candidate.conversation_event_ids.append(event.id)

        if event.event_type in cls._HUMAN_MESSAGE_TYPES and event.actor_id != bot_actor_id:
            candidate.self_social_state.consecutive_bot_messages = 0
            candidate.self_social_state.human_messages_since_bot += 1
            cls._record_person(candidate, event)
            GroupRegisterState.observe(candidate.group_register, event.raw_text)
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

    @staticmethod
    def _prune_expired_retained(state: GroupAgentSession, now: float) -> None:
        """ADR-0034: drop expired retained attention inside the lawful commit path."""
        if not state.retained_attention:
            return
        kept = [
            item
            for item in state.retained_attention
            if item.expires_at is None or item.expires_at > now
        ]
        if len(kept) != len(state.retained_attention):
            state.retained_attention = kept

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
        cls._prune_expired_retained(candidate, time.time())
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
        return candidate

    @classmethod
    def apply_fast_cognition(
        cls,
        state: GroupAgentSession,
        through_event_rowid: int,
    ) -> GroupAgentSession:
        """ADR-0038: FAST commits immediate state only — cursor advance and
        retained-attention pruning. Subjective social world is deferred."""
        candidate = state.model_copy(deep=True)
        candidate.version += 1
        candidate.last_cognized_event_rowid = through_event_rowid
        cls._prune_expired_retained(candidate, time.time())
        return candidate

    @classmethod
    def apply_deferred_patch(
        cls,
        state: GroupAgentSession,
        patch: SocialWorldPatch,
    ) -> GroupAgentSession:
        """ADR-0038: merge a deferred reflection patch into the session.

        Merge-only: add/close topics by id, append dynamics and identity facts,
        never wholesale replace — a later FULL cognition snapshot stays authoritative.
        """
        world = state.social_world.model_copy(deep=True)
        if patch.mood is not None:
            world.mood = patch.mood
        if patch.activity is not None:
            world.activity = patch.activity
        topics_by_id = {topic.id: topic for topic in world.topics}
        for topic in patch.open_topics:
            topics_by_id[topic.id] = topic
        for topic_id in patch.close_topic_ids:
            if topic_id in topics_by_id:
                topics_by_id[topic_id].status = "closed"
        world.topics = list(topics_by_id.values())
        for dynamic in patch.social_dynamics_add:
            if dynamic not in world.social_dynamics:
                world.social_dynamics.append(dynamic)
        world.social_dynamics = world.social_dynamics[-20:]

        identity = state.group_identity.model_copy(deep=True)
        if patch.group_identity is not None:
            if patch.group_identity.familiarity:
                identity.familiarity = patch.group_identity.familiarity
            for key, addition in (
                ("common_topics", patch.group_identity.common_topics_add),
                ("internal_expressions", patch.group_identity.internal_expressions_add),
                ("norms", patch.group_identity.norms_add),
            ):
                merged = list(getattr(identity, key))
                for item in addition:
                    if item not in merged:
                        merged.append(item)
                setattr(identity, key, merged[-20:])

        state.social_world = world
        state.group_identity = identity
        return state
