# Scenario Runner and Mock OneBot for Deterministic Offline Testing

> **Status — 2026-09-05:** Partially historical. `ScenarioRunner` is real and in use (offline deterministic replay via the mock send adapter and `mock_social_handler`); the named `MockOneBot`/JSONL replay never materialized — recorded-window replay is `ReplayLab` (ADR-0022) — and the "attention decisions" test target was removed with the V3 attention module.

Testing social agent behavior against a live QQ account is fragile, non-deterministic, and risks account bans. We decided to build a first-class `ScenarioRunner` and `MockOneBot` harness that replays timestamped JSONL conversation scenarios directly into the runtime. This enables automated, deterministic testing of scene versioning, attention decisions, and gate outcomes (including Section 106's livestream benchmark scenario) without live network dependencies.
