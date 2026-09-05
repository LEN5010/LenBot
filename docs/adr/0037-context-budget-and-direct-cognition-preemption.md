# ADR-0037: Token-Budgeted Social Context and Direct Cognition Preemption

- Status: Accepted
- Date: 2026-09-05

Social continuity benefits from retaining raw conversation, but a fixed message-count window wastes context on long OneBot media URLs and discards useful short messages prematurely. `SocialCoreContextAssembler` therefore projects protocol-only CQ payloads into compact semantic markers while leaving immutable events untouched, then packs recent events from newest to oldest against an estimated 200K-token input window with an explicit output reserve. Rollover removes oldest projected raw messages only after the budget is reached; structured session state remains directly present and older raw history remains available through agentic retrieval.

A direct mention or reply arriving while the same scene is waiting on an older model call makes that call certain to fail freshness validation. The runtime may cancel only that in-flight inference and restart cognition over the merged direct burst. It does not interrupt a SceneActor commit, RuntimeGate evaluation, or ActionQueue delivery. This is deterministic structural scheduling—not a second social attention policy—and the restarted Social Core remains the sole authority for speak/silence proposals.
