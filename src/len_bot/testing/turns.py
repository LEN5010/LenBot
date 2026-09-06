"""Deterministic typed proposals for isolated runtime boundary checks."""
from len_bot.cognition.models import EpisodeOutcome,FinalDisposition,MessageProposal


def turn_result(*,reason,content=None,**message_fields):
    return EpisodeOutcome(disposition=FinalDisposition.ACTION if content is not None else FinalDisposition.SILENCE,
        decision_reason=reason,message_proposals=[MessageProposal(segments=[{'type':'text','text':content}],**message_fields)] if content is not None else [])
