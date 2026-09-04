"""Layer 3 Model Evaluation Script.

Usage:
    python scripts/eval_transcripts.py --transcript path/to/transcript.jsonl [--output eval_results.json]

Takes a recorded transcript (JSONL format with raw events), replays it through
ReplayLab, evaluates with the live or configured model provider, and emits
per-message evaluation JSON (Attention disposition, reason, Prompt, Outcome,
decision_reason, and would-send) for human annotation.
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
from len_bot.events.models import Event
from len_bot.testing.replay import ReplayLab


async def main():
    parser = argparse.ArgumentParser(description="Evaluate transcript against ReplayLab & Model Provider")
    parser.add_argument("--transcript", required=True, help="Path to transcript.jsonl file")
    parser.add_argument("--output", default="eval_results.json", help="Path to write evaluation results")
    parser.add_argument("--scene-id", default="", help="Filter events by scene_id")
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
    lab = ReplayLab(config=config)
    results = await lab.replay(events)

    out_data = {
        "total_events": len(events),
        "results": results
    }

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)

    print(f"Evaluation complete! Results written to {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
