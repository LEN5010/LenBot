"""Token counts that services actually reported, per call and summed; no prices."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int | None
    completion_tokens: int | None
    cached_tokens: int | None


def token_record(usage: TokenUsage | None) -> dict[str, int | None] | None:
    """The stored token count of one call; None when the service did not report input and output."""
    if usage is None or usage.prompt_tokens is None or usage.completion_tokens is None:
        return None
    return {"input": usage.prompt_tokens, "output": usage.completion_tokens, "cached": usage.cached_tokens}


def token_summary(records: list[dict | None]) -> dict:
    """Sum stored records. Cached input is part of input; it is summed only where reported."""
    known = [record for record in records if record is not None]
    return {"input": sum(record["input"] for record in known),
            "output": sum(record["output"] for record in known),
            "cached": sum(record["cached"] for record in known if record["cached"] is not None),
            "known_calls": len(known), "unknown_calls": len(records) - len(known)}
