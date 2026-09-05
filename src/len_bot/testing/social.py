from len_bot.cognition.session import (
    SelfSocialStateUpdate,
    SocialCognitionResult,
    SocialDecision,
    SocialDecisionAction,
    SocialMessageProposal,
    SocialPerception,
    SocialWorldPatch,
)


def social_result(
    *,
    reason: str,
    content: str | None = None,
    summary: str = "understood",
    **proposal_fields,
) -> SocialCognitionResult:
    """Small deterministic result factory for runtime boundary tests."""
    action = SocialDecisionAction.SPEAK if content is not None else SocialDecisionAction.SILENCE
    messages = (
        [SocialMessageProposal(content=content, **proposal_fields)]
        if content is not None
        else []
    )
    return SocialCognitionResult(
        perception=SocialPerception(summary=summary, world_patch=SocialWorldPatch()),
        self_state=SelfSocialStateUpdate(
            engagement="observing",
            social_position="observer",
            current_interest="unknown",
            inclination_to_speak="low" if content is None else "high",
        ),
        decision=SocialDecision(action=action, reason=reason),
        message_proposals=messages,
    )
