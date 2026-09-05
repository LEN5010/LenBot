from __future__ import annotations

import time
from enum import StrEnum
from typing import ClassVar, Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from len_bot.cognition.projection import project_onebot_text
from len_bot.events.models import Event, EventType
from len_bot.memory.models import MemoryChange


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
    initiator_id: str | None = None
    addressed_to: list[str] = Field(default_factory=list)
    unresolved: str = ""


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
    feedback_event_ids: list[str] = Field(default_factory=list)


class GroupIdentity(SessionModel):
    familiarity: str = "unknown"
    common_topics: list[str] = Field(default_factory=list)
    internal_expressions: list[str] = Field(default_factory=list)
    usual_role: str = "observer"
    norms: list[str] = Field(default_factory=list)


class WorkingPersonModel(SessionModel):
    actor_id: str
    display_name: str | None = None
    nickname: str | None = None
    card: str | None = None
    preferred_name: str | None = None
    group_role: str | None = None
    recent_context: list[str] = Field(default_factory=list)
    recent_event_ids: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)


class RelationshipModel(SessionModel):
    actor_id: str
    familiarity: str = "unknown"
    patterns: list[str] = Field(default_factory=list)
    recent_interaction_event_ids: list[str] = Field(default_factory=list)
    memory_ids: list[str] = Field(default_factory=list)


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
    usual_role: str | None = None
    common_topics_add: list[str] = Field(default_factory=list)
    common_topics_remove: list[str] = Field(default_factory=list)
    internal_expressions_add: list[str] = Field(default_factory=list)
    internal_expressions_remove: list[str] = Field(default_factory=list)
    norms_add: list[str] = Field(default_factory=list)
    norms_remove: list[str] = Field(default_factory=list)


class SocialWorldPatch(SessionModel):
    """Sparse, revisable understanding. Actor validates its social revision."""

    mood: str | None = None
    activity: str | None = None
    open_topics: list[TopicState] = Field(default_factory=list)
    close_topic_ids: list[str] = Field(default_factory=list)
    social_dynamics_add: list[str] = Field(default_factory=list)
    social_dynamics_remove: list[str] = Field(default_factory=list)
    group_identity: GroupIdentityPatch | None = None
    source_event_ids: list[str] = Field(default_factory=list)
    open_threads: list[SocialThreadState] = Field(default_factory=list)
    latent_expectations: list[LatentExpectation] = Field(default_factory=list)
    remove_expectation_ids: list[str] = Field(default_factory=list)


class SocialDecisionAction(StrEnum):
    SILENCE = "silence"
    SPEAK = "speak"


class SelfSocialStateUpdate(SessionModel):
    engagement: str | None = None
    social_position: str | None = None
    current_interest: str | None = None
    inclination_to_speak: str | None = None
    recent_feedback_add: list[str] = Field(default_factory=list)
    recent_feedback_remove: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def feedback_evidence(self):
        if (self.recent_feedback_add or self.recent_feedback_remove) and not self.source_event_ids:
            raise ValueError("Interaction feedback needs source_event_ids")
        return self


class WorkingPersonUpdate(SessionModel):
    actor_id: str
    preferred_name: str | None = None
    recent_context_add: list[str] = Field(default_factory=list)
    recent_context_remove: list[str] = Field(default_factory=list)
    memory_ids_add: list[str] = Field(default_factory=list)
    memory_ids_remove: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def address_evidence(self):
        if (self.preferred_name is not None or self.recent_context_add or self.recent_context_remove
                or self.memory_ids_add or self.memory_ids_remove) and not self.source_event_ids:
            raise ValueError("Person understanding needs source_event_ids")
        return self


class RelationshipUpdate(SessionModel):
    actor_id: str
    familiarity: str | None = None
    patterns_add: list[str] = Field(default_factory=list)
    patterns_remove: list[str] = Field(default_factory=list)
    memory_ids_add: list[str] = Field(default_factory=list)
    memory_ids_remove: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def relationship_evidence(self):
        if (self.familiarity is not None or self.patterns_add or self.patterns_remove
                or self.memory_ids_add or self.memory_ids_remove) and not self.source_event_ids:
            raise ValueError("Relationship understanding needs source_event_ids")
        return self


class SocialPerception(SessionModel):
    summary: str
    world_patch: SocialWorldPatch = Field(default_factory=SocialWorldPatch)
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
    task_ref: str | None = None
    fulfils_task_id: str | None = None
    job_id: str | None = None
    job_revision: int | None = None

    @field_validator("reply_to", mode="before")
    @classmethod
    def normalize_onebot_message_id(cls, value):
        return str(value) if value is not None else None


class SocialTaskProposal(SessionModel):
    operation: Literal["create", "update", "cancel", "result", "fail"] = "create"
    task_id: str | None = None
    proposal_id: str | None = None
    due_at: float | None = None
    requester_id: str | None = None
    target_actor_id: str | None = None
    source_event_ids: list[str] = Field(default_factory=list)
    result: str | None = None
    description: str = ""
    delay_seconds: float | None = None
    wake_event_type: str | None = None
    wake_match: dict[str, object] | None = None
    payload: dict[str, object] = Field(default_factory=dict)

    @field_validator("due_at", mode="before")
    @classmethod
    def absolute_time(cls, value):
        if isinstance(value, str):
            instant = datetime.fromisoformat(value)
            if instant.tzinfo is None:
                raise ValueError("due_at must include a timezone offset")
            return instant.timestamp()
        return value

    @model_validator(mode="after")
    def task_contract(self):
        if self.payload.get("kind") == "next_wake":
            raise ValueError("next_wake is reserved for future_attention")
        if self.payload.get("kind") == "agent_job":
            raise ValueError("agent_job is reserved for job_proposals")
        if self.operation == "create":
            if not self.proposal_id or not self.source_event_ids:
                raise ValueError("Task creation requires proposal_id and source_event_ids")
            if self.due_at is None and self.wake_event_type is None:
                raise ValueError("Task requires absolute due_at or a wake event condition")
        elif not self.task_id:
            raise ValueError("Task operation requires a task_id")
        return self


SocialMemoryCandidate = MemoryChange


class RetainedAttentionProposal(SessionModel):
    summary: str
    source_event_ids: list[str]
    expires_at: float | None = None


class NextWakeIntentProposal(SessionModel):
    reason: str
    wake_at: float
    source_event_ids: list[str]


from len_bot.cognition.jobs import JobProposal


class SocialCognitionResult(SessionModel):
    perception: SocialPerception
    self_state: SelfSocialStateUpdate | None = None
    decision: SocialDecision
    message_proposals: list[SocialMessageProposal] = Field(default_factory=list)
    task_proposals: list[SocialTaskProposal] = Field(default_factory=list)
    job_proposals: list[JobProposal] = Field(default_factory=list)
    resolve_open_loop_ids: list[str] = Field(default_factory=list)
    retained_attention: list[RetainedAttentionProposal] = Field(default_factory=list)
    future_attention: NextWakeIntentProposal | None = None
    memory_candidates: list[SocialMemoryCandidate] = Field(default_factory=list)

    def requires_fresh_input(self) -> bool:
        return bool(self.task_proposals or self.job_proposals or self.future_attention or self.resolve_open_loop_ids
                    or any(m.task_ref or m.fulfils_task_id or m.expect_reply or m.job_id for m in self.message_proposals))

    @model_validator(mode="after")
    def validate_decision_contract(self) -> "SocialCognitionResult":
        if self.decision.action == SocialDecisionAction.SPEAK and not self.message_proposals:
            raise ValueError("speak requires at least one message proposal")
        if self.decision.action == SocialDecisionAction.SILENCE and self.message_proposals:
            raise ValueError("silence cannot contain message proposals")
        if any(proposal.payload.get("kind") == "next_wake" for proposal in self.task_proposals):
            raise ValueError("next_wake is reserved for future_attention")
        return self

    def to_episode_outcome(self, scene_id: str, through_event_rowid: int, now: float | None = None):
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
                    delay_seconds=max(0.0, self.future_attention.wake_at - (time.time() if now is None else now)),
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
            job_proposals=self.job_proposals,
            memory_proposals=[
                MemoryProposal(scope=scene_id, **candidate.model_dump())
                for candidate in self.memory_candidates
            ],
            resolve_open_loop_ids=self.resolve_open_loop_ids,
        )


class GroupAgentSession(SessionModel):
    social_revision: int = 0
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
    recent_memory_changes: list[dict] = Field(default_factory=list)


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

        if event.event_type == EventType.REFLECTION_RECORDED:
            if event.payload.get("social_revision") == state.social_revision:
                candidate.recent_episode_summary = event.payload.get("episode_summary", "")
                if event.payload.get("patch"):
                    cls.apply_deferred_patch(candidate, SocialWorldPatch.model_validate(event.payload["patch"]))
                cls.apply_memory_receipts(candidate, event.payload.get("memory_receipts", []))
                candidate.social_revision += 1
            return candidate

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

        if "nickname" in sender:
            person.nickname = str(sender["nickname"])
        if "card" in sender:
            person.card = str(sender["card"])
        display_name = person.card or person.nickname
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
        now: float | None = None,
    ) -> GroupAgentSession:
        candidate = state.model_copy(deep=True)
        candidate.version += 1
        candidate.last_cognized_event_rowid = through_event_rowid
        candidate.social_revision += 1
        cls._prune_expired_retained(candidate, time.time() if now is None else now)
        cls.apply_deferred_patch(candidate, result.perception.world_patch)

        update = result.self_state
        self_state = candidate.self_social_state
        if update is not None:
            for name in ("engagement", "social_position", "current_interest", "inclination_to_speak"):
                if getattr(update, name) is not None:
                    setattr(self_state, name, getattr(update, name))
            self_state.recent_feedback = cls._update_list(self_state.recent_feedback,
                update.recent_feedback_add, update.recent_feedback_remove)
            self_state.feedback_event_ids = cls._update_list(self_state.feedback_event_ids, update.source_event_ids, [])

        for person_update in result.perception.person_updates:
            person = candidate.working_persons.get(person_update.actor_id)
            if person is None:
                person = WorkingPersonModel(actor_id=person_update.actor_id)
            if person_update.preferred_name is not None:
                person.preferred_name = person_update.preferred_name
            person.recent_context = cls._update_list(person.recent_context,
                person_update.recent_context_add, person_update.recent_context_remove)
            person.memory_ids = cls._update_list(person.memory_ids,
                person_update.memory_ids_add, person_update.memory_ids_remove)
            person.recent_event_ids = list(dict.fromkeys(person.recent_event_ids + person_update.source_event_ids))
            candidate.working_persons[person_update.actor_id] = person

        for relationship_update in result.perception.relationship_updates:
            relationship = candidate.working_relationships.get(relationship_update.actor_id) or RelationshipModel(actor_id=relationship_update.actor_id)
            if relationship_update.familiarity is not None:
                relationship.familiarity = relationship_update.familiarity
            relationship.patterns = cls._update_list(relationship.patterns,
                relationship_update.patterns_add, relationship_update.patterns_remove)
            relationship.memory_ids = cls._update_list(relationship.memory_ids,
                relationship_update.memory_ids_add, relationship_update.memory_ids_remove)
            relationship.recent_interaction_event_ids = cls._update_list(
                relationship.recent_interaction_event_ids, relationship_update.source_event_ids, [])
            candidate.working_relationships[relationship.actor_id] = relationship

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

    @staticmethod
    def _update_list(current: list, additions: list, removals: list) -> list:
        return list(dict.fromkeys([x for x in current if x not in removals] + additions))

    @classmethod
    def apply_memory_receipts(cls, state: GroupAgentSession, receipts: list[dict]) -> None:
        """Runtime commit receipts are pointers to beliefs, not retrieved generic context."""
        for receipt in receipts:
            removed = receipt["target_memory_ids"]
            state.recent_memory_changes = [item for item in state.recent_memory_changes
                                           if item["id"] not in removed and item["id"] != receipt["id"]]
            for entry in [*state.working_persons.values(), *state.working_relationships.values()]:
                entry.memory_ids = [mid for mid in entry.memory_ids if mid not in removed]
            person = state.working_persons.get(receipt["subject"])
            if person is not None and receipt["status"] == "active":
                person.memory_ids = cls._update_list(person.memory_ids, [receipt["id"]], [])
            state.recent_memory_changes.append(receipt)
        state.recent_memory_changes = state.recent_memory_changes[-20:]

    @classmethod
    def apply_deferred_patch(
        cls,
        state: GroupAgentSession,
        patch: SocialWorldPatch,
    ) -> GroupAgentSession:
        """Apply an evidence-backed sparse patch after the actor's revision check."""
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
        world.social_dynamics = cls._update_list(world.social_dynamics,
            patch.social_dynamics_add, patch.social_dynamics_remove)[-20:]

        identity = state.group_identity.model_copy(deep=True)
        if patch.group_identity is not None:
            if patch.group_identity.familiarity:
                identity.familiarity = patch.group_identity.familiarity
            if patch.group_identity.usual_role is not None:
                identity.usual_role = patch.group_identity.usual_role
            for key, addition in (
                ("common_topics", patch.group_identity.common_topics_add),
                ("internal_expressions", patch.group_identity.internal_expressions_add),
                ("norms", patch.group_identity.norms_add),
            ):
                merged = cls._update_list(getattr(identity, key), addition,
                    getattr(patch.group_identity, key + "_remove"))
                setattr(identity, key, merged[-20:])

        threads = {item.id: item for item in world.open_threads}
        threads.update({item.id: item for item in patch.open_threads})
        world.open_threads = list(threads.values())
        expectations = {item.id: item for item in world.latent_expectations
                        if item.id not in patch.remove_expectation_ids}
        expectations.update({item.id: item for item in patch.latent_expectations})
        world.latent_expectations = list(expectations.values())
        state.social_world = world
        state.group_identity = identity
        return state
