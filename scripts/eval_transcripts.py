"""Layer 3 Model Evaluation Script.

Usage:
    python scripts/eval_transcripts.py --transcript path/to/transcript.jsonl [--output eval_results.json]

Replays recorded events through BurstAssembler, SceneActor, Social Core, tools,
RuntimeGate and Shadow ActionQueue in an isolated temporary database.
Live mode requires explicit provider configuration; --scripted is an offline
plumbing check, not an intelligence evaluation. --database and --provider-db
must point to read-only SQLite snapshots, not a concurrently modified WAL file.
"""

import argparse
import asyncio
import json
import os
import sys
import sqlite3
from pathlib import Path

# Add src to pythonpath
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from len_bot.config import RuntimeConfig
from len_bot.cognition.providers import ProviderConfig, ProviderRegistry, RouteTarget, RoutingConfig
from len_bot.cognition.session import SocialCognitionResult
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.events.models import Event
from len_bot.testing.replay import ReplayLab
from len_bot.testing.social import social_result


async def _scripted_social_handler(messages: list[dict[str, str]]) -> SocialCognitionResult:
    """Deterministic offline Social Core: answer only when directly addressed."""
    burst = messages[-1]["content"]
    if "MentionBot: True" in burst or "ReplyBot: True" in burst:
        return social_result(
            summary="用户直接 @ 或回复了 Bot",
            reason="被直接点名，需要回应",
            content="收到",
        )
    return social_result(summary="群友闲聊", reason="没有自然插话位置")


async def _build_social_core(config: RuntimeConfig, scripted: bool, provider_db: str | None = None) -> tuple[SocialCognitionCore, bool]:
    registry = ProviderRegistry()
    if not scripted and provider_db:
        with sqlite3.connect(Path(provider_db).resolve().as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT value_json FROM runtime_dynamic_configs WHERE key='provider_config'").fetchone()
            if row is None:
                raise ValueError("Snapshot has no provider configuration")
            saved = json.loads(row[0])
            saved["routing"].pop("fast", None)
            await registry.apply_update([ProviderConfig(**p) for p in saved["providers"]], RoutingConfig(**saved["routing"]))
            row = db.execute("SELECT value_json FROM runtime_dynamic_configs WHERE key='persona_config'").fetchone()
            if row:
                for key, value in json.loads(row[0]).items():
                    if key in {"identity_name", "identity_core", "identity_persona", "conversation_style", "character_context", "bot_qq"}:
                        setattr(config, key, value)
    elif not scripted:
        seed_provider = ProviderConfig(
            id="default",
            base_url=config.openai_base_url,
            api_key=config.openai_api_key,
        )
        seed_routing = RoutingConfig(
            normal=RouteTarget(provider_id="default", model=config.default_model),
            deliberate=RouteTarget(provider_id="default", model=config.deliberate_model),
        )
        await registry.apply_update([seed_provider], seed_routing)
    if not scripted:
        if not registry.has_live_provider():
            raise RuntimeError("Live evaluation requires a provider; use --scripted explicitly for offline fixtures")
    core = SocialCognitionCore(
        config=config,
        registry=registry,
        mock_handler=_scripted_social_handler if scripted else None,
    )
    return core, scripted


async def main():
    parser = argparse.ArgumentParser(description="Evaluate transcript against ReplayLab & Model Provider")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--transcript", help="Path to transcript.jsonl file")
    source.add_argument("--database", help="Read-only SQLite transcript snapshot")
    parser.add_argument("--provider-db", help="Read-only snapshot containing operator-approved provider routes")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--since", type=float, default=0)
    parser.add_argument("--until", type=float, default=None)
    parser.add_argument("--output", default="eval_results.json", help="Path to write evaluation results")
    parser.add_argument("--scene-id", default="", help="Filter events by scene_id")
    parser.add_argument("--tools", choices=["mock", "real"], default="mock")
    parser.add_argument("--scripted", action="store_true",
                        help="Force the deterministic scripted Social Core (offline, no provider calls)")
    args = parser.parse_args()

    if args.database:
        with sqlite3.connect(Path(args.database).resolve().as_uri() + "?mode=ro", uri=True) as db:
            records = db.execute(
                """SELECT id,event_type,scene_id,actor_id,timestamp,payload,metadata FROM events
                   WHERE timestamp>=? AND timestamp<=? AND (?='' OR scene_id=?)
                   AND event_type IN ('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED','MESSAGE_SENT','LIVE_STARTED','LIVE_ENDED')
                   ORDER BY timestamp,rowid LIMIT ?""",
                (args.since, args.until or 1e18, args.scene_id, args.scene_id, args.limit))
            events = [Event(id=r[0], event_type=r[1], scene_id=r[2], actor_id=r[3], timestamp=r[4],
                            payload=json.loads(r[5]), metadata=json.loads(r[6])) for r in records]
    else:
        with open(args.transcript, encoding="utf-8") as transcript:
            events = [Event.model_validate_json(line) for line in transcript if line.strip()]

    if args.scene_id:
        events = [e for e in events if e.scene_id == args.scene_id]

    print(f"Loaded {len(events)} events for evaluation...")

    config = RuntimeConfig()
    social_core, scripted = await _build_social_core(config, args.scripted, args.provider_db)
    print(f"Social Core mode: {'scripted (offline)' if scripted else 'live provider'}")

    examples = []
    if args.provider_db:
        with sqlite3.connect(Path(args.provider_db).resolve().as_uri() + "?mode=ro", uri=True) as db:
            examples = [dict(zip(("scene_id", "content", "context", "tag"), row)) for row in db.execute(
                "SELECT scene_id,content,context,tag FROM voice_exemplars WHERE enabled=1")]
    lab = ReplayLab(config=config, social_core=social_core, tool_mode=args.tools, voice_examples=examples)
    results = await lab.run(events, until=args.until)

    out_data = {
        "total_events": len(events),
        "mode": "scripted" if scripted else "live",
        "results": results,
        "metrics": lab.last_metrics,
        "tasks": lab.last_tasks,
        "traces": lab.last_traces,
        "evaluation_complete": not any(t["kind"] == "social_cognition_error" for t in lab.last_traces),
        "human_review": [{"event_id": r["event_id"], "wrong_person": None, "wrong_thread": None,
                          "continuity_error": None, "missed_participation": None,
                          "unnecessary_speech": None, "bot_like": None, "timing_error": None,
                          "comment": ""} for r in results],
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(f"Evaluation results written to {args.output}")
    if not out_data["evaluation_complete"]:
        raise RuntimeError("Evaluation contains model/runtime failures; inspect traces, do not score it as silence")


if __name__ == "__main__":
    asyncio.run(main())
