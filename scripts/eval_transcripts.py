"""Layer 3 Model Evaluation Script.

Usage:
    python scripts/eval_transcripts.py --transcript path/to/transcript.jsonl [--output eval_results.json]

Takes a recorded transcript (JSONL format with raw events), replays it through the
V4 ReplayLab (pure GroupAgentSession reducer + SocialCognitionCore), and emits
per-event evaluation JSON (decision, reason, understanding, would-send, trace)
for human annotation.

Mode selection: when a live provider key is configured (OPENAI_API_KEY), the real
SocialCognitionCore LLM call is used. Otherwise — or when --scripted is passed —
a deterministic scripted Social Core runs offline (no provider calls).
"""

import argparse
import asyncio
import json
import os
import sys
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


async def _build_social_core(config: RuntimeConfig, scripted: bool) -> tuple[SocialCognitionCore, bool]:
    registry = ProviderRegistry()
    if not scripted:
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
        if not registry.has_live_provider():
            scripted = True
    core = SocialCognitionCore(
        config=config,
        registry=registry,
        mock_handler=_scripted_social_handler if scripted else None,
    )
    return core, scripted


async def main():
    parser = argparse.ArgumentParser(description="Evaluate transcript against ReplayLab & Model Provider")
    parser.add_argument("--transcript", required=True, help="Path to transcript.jsonl file")
    parser.add_argument("--output", default="eval_results.json", help="Path to write evaluation results")
    parser.add_argument("--scene-id", default="", help="Filter events by scene_id")
    parser.add_argument("--scripted", action="store_true",
                        help="Force the deterministic scripted Social Core (offline, no provider calls)")
    args = parser.parse_args()

    if not os.path.exists(args.transcript):
        print(f"Error: Transcript file not found: {args.transcript}")
        sys.exit(1)

    events = []
    with open(args.transcript, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                events.append(Event(**data))
            except Exception as e:
                print(f"Warning: skipped invalid line: {e}")

    if args.scene_id:
        events = [e for e in events if e.scene_id == args.scene_id]

    print(f"Loaded {len(events)} events for evaluation...")

    config = RuntimeConfig()
    social_core, scripted = await _build_social_core(config, args.scripted)
    print(f"Social Core mode: {'scripted (offline)' if scripted else 'live provider'}")

    lab = ReplayLab(config=config, social_core=social_core)
    results = await lab.run(events)

    out_data = {
        "total_events": len(events),
        "mode": "scripted" if scripted else "live",
        "results": results
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(f"Evaluation complete! Results written to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
