# ADR-0031: Monitored Keywords Hot Reload, Shadow Annotations Persistence, and OneBot Adapter Robustness

## Status

Partially superseded — 2026-09-05. §1's `monitored_keywords` / `AttentionEngine` hot-reload was removed with the V3 attention module. §2 and §3 remain active; note that `GET /api/cockpit/shadow-annotations` supports a `scene_id` filter only (no label filter), and the planned message-ID→reply-ID ring was removed as dead code — quote replies use the OneBot message id that the cognition context already exposes (`OneBotMessageID=`), while reply-bot detection relies on the bot's own sent-ID ring.

## Context

In V2:
1. `POST /api/cockpit/social` updated `config.monitored_keywords`, `speaking_budget.base_threshold`, and `interest_model.topics`, but `AttentionEngine` holds its own reference to `monitored_keywords`. Updating social config didn't immediately reflect in attention checks.
2. In Shadow Mode, would-send records were recorded in memory, but human operators in the Control Plane had no way to annotate decisions (True Positive, False Positive, True Negative, False Negative + reviewer comments) and query these labels for evaluation and accuracy metrics.
3. The OneBot v11 adapter needed hardening for NapCat/Lagrange:
   - Recording message ID to reply ID mapping for proper CQ-code quote replies.
   - Dropping bot's own echo events when `user_id == bot_qq` or `post_type == "message_sent"` to prevent self-loop triggers.
   - Exponential backoff reconnection (1s to 30s) on WebSocket drops.

## Decision

1. **monitored_keywords Hot Reload**:
   - In `AttentionEngine`: expose `self.monitored_keywords: list[str]`.
   - In `AgentRuntime.update_social_config()`:
     * Save to `social_config` in SQLite.
     * Update `self.config.monitored_keywords`.
     * Immediately set `self.attention_engine.monitored_keywords = list(keywords)`.

2. **Shadow Annotations Persistence**:
   - Create SQLite table `shadow_annotations`:
     `CREATE TABLE IF NOT EXISTS shadow_annotations (
         id TEXT PRIMARY KEY,
         stimulus_id TEXT,
         scene_id TEXT NOT NULL,
         label TEXT NOT NULL, -- TP, FP, TN, FN
         comment TEXT,
         created_at REAL NOT NULL
     );`
   - Add routes:
     * `POST /api/cockpit/shadow-annotations`: create or update annotation.
     * `GET /api/cockpit/shadow-annotations`: query annotations with optional scene/label filters and summary stats.

3. **NapCat OneBot Adapter Hardening**:
   - Filter out self-messages (`event.get("user_id") == self.bot_qq` or `post_type == "message_sent"`).
   - Maintain message ID cache for quote-reply CQ code formatting: `[CQ:reply,id={reply_to}]`.
   - Exponential backoff retry on disconnect (base 1s, doubling up to 30s max).

## Consequences

- Social configuration hot reloads across all attention subsystems without restarting the process.
- Shadow testing enables quantitative human feedback and accuracy metrics.
- OneBot integration avoids echo loops and recovers robustly from connection interruptions.
