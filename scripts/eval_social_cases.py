"""Fixed pre-cutoff comparisons; real Social Core through production Shadow pipeline.

No production database writes or QQ connection. --scripted is a plumbing check,
never a substitute for a failed real-model run. Compare the same fixture using
--source-root pointing to the pre-change source and the current source.
"""
import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


async def run(args):
    sys.path.insert(0, str(Path(args.source_root).resolve()))
    from len_bot.cognition.diana import PERSONA, EXAMPLES, PRESET_ID
    from len_bot.cognition.providers import ProviderConfig, ProviderRegistry, RoutingConfig
    from len_bot.cognition.social_core import SocialCognitionCore
    from len_bot.config import RuntimeConfig
    from len_bot.events.models import Event
    from len_bot.testing.social import social_result
    # Use the current test harness with the selected production implementation.
    spec = importlib.util.spec_from_file_location("case_replay", ROOT / "src/len_bot/testing/replay.py")
    replay = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(replay)
    fixture = json.loads(Path(args.fixture).read_text())
    events = [Event.model_validate(e) for e in fixture["events"]]
    config = RuntimeConfig(bot_qq=fixture["bot_qq"], **PERSONA)
    registry = ProviderRegistry()
    if not args.scripted:
        if not args.provider_db:
            raise ValueError("Real evaluation requires --provider-db; no scripted fallback")
        db_path = Path(args.provider_db).resolve()
        if Path(str(db_path) + "-wal").exists():
            raise ValueError("Use a stopped, checkpointed provider DB; do not read a live WAL snapshot")
        with sqlite3.connect(db_path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            row = db.execute("SELECT value_json FROM runtime_dynamic_configs WHERE key='provider_config'").fetchone()
        db.close()
        if row is None:
            raise ValueError("Provider configuration not found")
        saved = json.loads(row[0])
        await registry.apply_update([ProviderConfig(**p) for p in saved["providers"]], RoutingConfig(**saved["routing"]))
        if not registry.has_live_provider():
            raise ValueError("No configured live provider")
    async def scripted(messages):
        return social_result(reason="离线链路检查，不评估智能", content="离线示例")
    core = SocialCognitionCore(config, registry, mock_handler=scripted if args.scripted else None)
    cases = fixture["cases"] if args.case == "all" else [c for c in fixture["cases"] if c["name"] == args.case]
    results = []
    for case in cases:
        index = next(i for i, e in enumerate(events) if e.id == case["event_id"])
        target, history = events[index], events[:index]
        known = {e.id for e in history}
        memories = [m for m in fixture["memories"] if m["created_at"] <= target.timestamp
                    and m["last_confirmed_at"] <= target.timestamp and set(m["evidence"]).issubset(known)]
        tool_fixture = {"mode": "replay_mock", "results": [], "coverage": "unknown"}
        lab = replay.ReplayLab(config, core, tool_mode="mock", tool_results={
            name: json.dumps(tool_fixture) for name in ("web_search", "read_page", "search_bilibili")
        }, voice_examples=[dict(scene_id="", context=context, content=content, tag="运营样例")
                           for context, content in EXAMPLES])
        started = time.monotonic()
        output = await lab.run([target], history=history, memories=memories)
        failure = any(t["kind"] == "social_cognition_error" for t in lab.last_traces)
        result = {"case": case["name"], "event_id": target.id, "cutoff": target.timestamp,
                  "history_event_count": len(history), "seed_memory_ids": lab.seed_memory_ids,
                  "source_memory_ids": [m["id"] for m in memories],
                  "elapsed_seconds": round(time.monotonic() - started, 2), "tool_fixture": tool_fixture,
                  "completed": bool(output) and not failure and all(o["trace"]["gate"]["accepted"] for o in output),
                  "outputs": output, "traces": lab.last_traces, "sessions": lab.last_sessions,
                  "memories": lab.last_memories, "metrics": lab.last_metrics,
                  "human_response_to_candidate": None, "human_engagement_with_candidate": None}
        results.append(result)
        Path(args.output).write_text(json.dumps({"mode": "scripted" if args.scripted else "live",
            "persona_preset": PRESET_ID, "source_root": str(Path(args.source_root).resolve()),
            "fixture": str(Path(args.fixture).resolve()), "results": results}, ensure_ascii=False, indent=2))
        print(f"{case['name']}: {'completed' if result['completed'] else 'FAILED'} ({result['elapsed_seconds']}s)", flush=True)
    if any(not result["completed"] for result in results):
        raise RuntimeError("Real/scripted pipeline failed; partial traces saved. No fallback to a scripted model.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(ROOT / "src"))
    parser.add_argument("--fixture", default=str(ROOT / "tests/fixtures/social_20260906.json"))
    parser.add_argument("--provider-db")
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument("--case", choices=["all", "gentler", "search_live", "preferred_name", "correct_person", "followup"], default="all")
    parser.add_argument("--output", required=True)
    asyncio.run(run(parser.parse_args()))
