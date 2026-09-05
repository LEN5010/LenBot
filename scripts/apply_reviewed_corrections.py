"""Apply a reviewed, evidence-backed silent correction to a STOPPED database.

Default is a read-only preview. --apply commits through Actor/RuntimeGate, never
starts AgentRuntime, a scheduler, an LLM, or QQ. It preserves the cognition cursor
so unread input is not silently marked understood. The cognition event's mode is
the atomic idempotency record; old events and memories are never deleted.
"""
import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from len_bot.actions.queue import ActionQueue
from len_bot.cognition.mailbox import EpisodeMailbox
from len_bot.cognition.session import SocialCognitionResult
from len_bot.events.store import EventStore
from len_bot.memory.store import MemoryStore
from len_bot.runtime.gate import RuntimeGate
from len_bot.scenes.actor import SceneActor


async def run(database, manifest, apply=False):
    path = Path(database).resolve()
    if Path(str(path) + "-wal").exists():
        raise ValueError("请先停止生产进程并完成 WAL checkpoint")
    plan = json.loads(Path(manifest).read_text())
    result = SocialCognitionResult.model_validate(plan["result"])
    if result.decision.action != "silence" or result.requires_fresh_input():
        raise ValueError("Correction manifest may not propose messages, tasks, wakes or OpenLoops")
    with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        done = db.execute("SELECT id FROM events WHERE scene_id=? AND json_extract(metadata,'$.mode')=?",
                          (plan["scene_id"], plan["id"])).fetchone()
        if done:
            print("此清单已原子提交；不重复应用。")
            return False
        for mid, expected in plan["expected_memories"].items():
            row = db.execute("SELECT value,status FROM memories WHERE id=? AND scope=?", (mid, plan["scene_id"])).fetchone()
            if row != (expected, "active"):
                raise ValueError(f"修订目标 {mid} 已变化，请重新核对清单")
    print(json.dumps(plan["result"], ensure_ascii=False, indent=2))
    if not apply:
        print("仅预览，未修改数据库。")
        return False
    store = EventStore(str(path))
    await store.initialize()
    memory = MemoryStore(store._db, store._write_lock)
    await memory.initialize()
    actor = SceneActor(plan["scene_id"], plan["bot_actor_id"], store)
    await actor.start()
    try:
        mailbox = EpisodeMailbox(plan["id"], actor.scene_id, actor.state.version)
        actor.acquire_episode_lease(plan["id"], mailbox)
        queue = ActionQueue(store)  # No consumer and no adapter; manifest is silent.
        evidence = list(dict.fromkeys(eid for change in result.memory_candidates for eid in change.evidence))
        decision = await actor.commit_cognitive_turn(result, actor.group_session.last_cognized_event_rowid,
            evidence, plan["id"], plan["id"], mailbox, RuntimeGate(store, queue),
            social_revision=actor.group_session.social_revision)
        if not decision or not decision.accepted:
            raise RuntimeError("修订被拒绝，未提交：" + (decision.reason if decision else "状态版本变化"))
        print("修订已提交；没有发送消息，没有推进未读认知游标。")
        return True
    finally:
        await actor.stop()
        await store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.database, args.manifest, args.apply))
