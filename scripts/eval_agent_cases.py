"""Interleaved generic-agent evaluation; no production writes or OneBot connection."""
import argparse
import hashlib
import asyncio
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace as NS

from len_bot.cognition.providers import ProviderConfig, ProviderRegistry, RoutingConfig, RouteTarget
from len_bot.cognition.social_core import SocialCognitionCore
from len_bot.config import RuntimeConfig
from len_bot.events.models import Event, EventType
from len_bot.testing.replay import ReplayLab, ReplayInjection
from len_bot.testing.social import social_result

ROOT = Path(__file__).resolve().parents[1]


class ScriptedRegistry:
    """Explicit simulated completions exercise real tool/actor paths, never grade intelligence."""
    is_simulated = True
    def has_live_provider(self):
        return True
    def resolve(self, tier):
        return NS(provider_id="scripted", model="scripted", client=NS(chat=NS(completions=self)))
    def resolve_fallback(self):
        return None
    async def create(self, **kwargs):
        if not any(m["role"] == "tool" for m in kwargs["messages"]):
            message = NS(content=None, tool_calls=[NS(id="scripted-query", function=NS(
                name="web_search", arguments='{"query":"public information"}'))])
        else:
            message = NS(content=social_result(reason="仅验证链路", content="脚本候选，不用于自然度或事实验收").model_dump_json(), tool_calls=None)
        return NS(choices=[NS(message=message)], usage=None)


async def load_registry(args):
    config = RuntimeConfig(bot_qq=9999)
    if args.scripted:
        return ScriptedRegistry(), config, []
    if not args.provider_db:
        raise ValueError("Real evaluation requires --provider-db")
    uri = Path(args.provider_db).resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        rows = dict(db.execute("SELECT key,value_json FROM runtime_dynamic_configs WHERE key IN ('provider_config','persona_config')"))
    saved = json.loads(rows.get("provider_config", "null"))
    if not saved:
        raise ValueError("No provider configuration; no scripted fallback")
    routing = RoutingConfig.model_validate(saved["routing"])
    if args.model:
        provider = next(p for p in saved["providers"] if p["id"] == routing.normal.provider_id)
        if args.model not in provider.get("models", []):
            raise ValueError("Comparison model must already be in the saved provider catalog")
        routing.normal = RouteTarget(provider_id=provider["id"], model=args.model)
        routing.deliberate = routing.normal.model_copy()
    # A comparison must not silently fall back to a different model.
    routing.fallback = None
    registry = ProviderRegistry()
    await registry.apply_update([ProviderConfig.model_validate(p) for p in saved["providers"]], routing)
    if not registry.has_live_provider():
        raise ValueError("No live provider")
    persona = json.loads(rows.get("persona_config", "{}"))
    config = config.model_copy(update={key: value for key, value in persona.items()
        if key in {"identity_name", "identity_persona", "identity_core", "conversation_style", "character_context"}})
    with sqlite3.connect(uri, uri=True) as db:
        examples = [dict(scene_id="", context=row[0], content=row[1], tag=row[2]) for row in db.execute(
            "SELECT context,content,tag FROM voice_exemplars WHERE enabled=1 AND scene_id IN (?,?) ORDER BY scene_id,created_at,id", ("",args.source_scene))]
    return registry, config, examples


async def run(args):
    registry, config, examples = await load_registry(args)
    fixture_text = Path(args.fixture).read_text()
    fixture_hash = hashlib.sha256(fixture_text.encode()).hexdigest()
    suite = json.loads(fixture_text)
    config.bot_qq = suite["bot_qq"]
    cases = [case for case in suite["cases"] if args.case == "all" or case["name"] in args.case.split(",")]
    if not cases:
        raise ValueError("Unknown case")
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    source_hash = hashlib.sha256()
    for path in sorted((ROOT / "src/len_bot").rglob("*.py")):
        source_hash.update(str(path.relative_to(ROOT)).encode())
        source_hash.update(path.read_bytes())
    results = []
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        for repeat in range(args.repeats):
            base = 1788681600.0  # Same time context for every model and repeat.
            def event(index, text, offset, actor_id="user:1001", payload=None):
                return Event(id=f"{case['name']}:{repeat}:{index}", scene_id=f"group:eval-{case['name']}",
                    event_type=EventType.GROUP_MESSAGE_RECEIVED, actor_id=actor_id, timestamp=base+offset,
                    payload={"message_id": str(index), "raw_text": text, "at_bot": True, **(payload or {})})
            inputs = [event(i, item["text"], item["offset"], item.get("actor_id","user:1001"), item.get("payload")) for i, item in enumerate(case["messages"])]
            injections = [ReplayInjection(item["checkpoint"], [event(f"injected-{i}", item["text"], item["offset"], item.get("actor_id","user:1001"), item.get("payload"))], item["occurrence"])
                          for i, item in enumerate(case["injections"])]
            lab = ReplayLab(config, SocialCognitionCore(config, registry), tool_mode=args.tool_mode,
                tool_results={name: json.dumps(value, ensure_ascii=False) for name, value in case["tool_results"].items()},
                delivery_mode="simulated", voice_examples=examples, injections=injections, strict=True, max_model_calls=args.max_model_calls)
            started, rows, error = time.monotonic(), [], None
            try:
                rows = await lab.run(inputs)
            except Exception as failure:
                error = f"{type(failure).__name__}: {failure}"
            record = {"case": case["name"], "repeat": repeat, "run": lab.last_run, "error": error,
                "elapsed_seconds": round(time.monotonic()-started, 3), "outputs": rows,
                "checkpoints": lab.last_checkpoints, "traces": lab.last_traces, "deliveries": lab.last_deliveries,
                "observations": lab.last_observations, "sessions": lab.last_sessions, "memories": lab.last_memories,
                "metrics": lab.last_metrics, "assessment": None, "human_response_to_candidate": None}
            record["unsupported_capabilities"] = sorted(set(case["required_capabilities"]) - set(lab.last_run.get("capabilities", [])))
            results.append(record)
            output.write_text(json.dumps({"schema_version": 1, "revision": revision,
                "source_tree_sha256": source_hash.hexdigest(),
                "fixture_sha256": fixture_hash,
                "voice_examples": examples, "model": args.model or "current", "model_mode": "mock" if args.scripted else "real",
                "tool_mode": args.tool_mode, "config": {key: getattr(config, key) for key in
                    ("identity_name", "identity_persona", "identity_core", "conversation_style", "character_context")},
                "notice": "完成只代表链路执行；脚本用户不是真实互动，assessment待人工评分。",
                "results": results}, ensure_ascii=False, indent=2))
            print(f"{case['name']} #{repeat+1}: {'FAILED' if error else 'pipeline completed'}", flush=True)
    if any(record["error"] for record in results):
        raise RuntimeError("Incomplete evaluation; all failures saved")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-db")
    parser.add_argument("--model")
    parser.add_argument("--source-scene", default="group:126300994")
    parser.add_argument("--scripted", action="store_true")
    parser.add_argument("--fixture", default=str(ROOT / "tests/fixtures/generic_agent_cases.json"))
    parser.add_argument("--case", default="all")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--tool-mode", choices=["mock", "real"], default="mock")
    parser.add_argument("--max-model-calls", type=int, default=100)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.repeats < 1 or args.max_model_calls < 1:
        parser.error("repeats and max-model-calls must be positive")
    asyncio.run(run(args))
