# Scenario Runner and Mock OneBot for Deterministic Offline Testing

Testing social agent behavior against a live QQ account is fragile, non-deterministic, and risks account bans. We decided to build a first-class `ScenarioRunner` and `MockOneBot` harness that replays timestamped JSONL conversation scenarios directly into the runtime. This enables automated, deterministic testing of scene versioning, attention decisions, and gate outcomes (including Section 106's livestream benchmark scenario) without live network dependencies.
