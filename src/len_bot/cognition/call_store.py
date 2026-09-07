"""Durable accounting for actual provider requests, without a billing claim."""

from __future__ import annotations

import json
import uuid

from len_bot.cognition.projection import estimate_tokens


def estimate_request(messages: list[dict], tools: list[dict]) -> dict:
    """One deliberately approximate estimator; image estimates are explicit."""
    parts = {"system_reference": 0, "history": 0, "tool_results": 0,
             "tool_definitions": estimate_tokens(json.dumps(tools, ensure_ascii=False)), "images": 0}
    images = 0
    for message in messages:
        text = {key:value for key,value in message.items() if key != '_context_section'}
        content = message.get("content")
        if isinstance(content, list):
            text["content"] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") in {"image_url", "input_image"}:
                    images += 1
                else:
                    text["content"].append(item)
        key = "system_reference" if (message.get("role") in {"system", "developer"}
            or message.get('_context_section') == 'reference') else (
            "tool_results" if message.get("role") == "tool" else "history")
        parts[key] += estimate_tokens(json.dumps(text, ensure_ascii=False, default=str))
    parts["images"] = images * 1024
    return {"method": "cjk-1.5-other-1/3-v1", "parts": parts,
            "input_tokens": sum(parts.values()), "image_count": images, "image_tokens_each": 1024}


class ModelCallStoreMixin:
    async def initialize_model_calls(self):
        await self._db.execute("""CREATE TABLE IF NOT EXISTS model_calls (
            id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, episode_id TEXT, job_id TEXT, batch_id TEXT,
            role TEXT NOT NULL, purpose TEXT NOT NULL, provider_id TEXT NOT NULL, model TEXT NOT NULL,
            reasoning_effort TEXT, started_at REAL NOT NULL, ended_at REAL,
            status TEXT NOT NULL, usage_json TEXT, estimate_json TEXT NOT NULL,
            output_estimate_tokens INTEGER, error_type TEXT, disposition TEXT)""")
        await self._db.execute("CREATE INDEX IF NOT EXISTS idx_model_calls_scene ON model_calls(scene_id,started_at)")

    async def begin_model_call(self, *, scene_id, episode_id, job_id, batch_id, role, purpose,
                               provider_id, model, reasoning_effort, estimate):
        call_id = uuid.uuid4().hex
        async with self._write_lock:
            await self._db.execute("""INSERT INTO model_calls
                (id,scene_id,episode_id,job_id,batch_id,role,purpose,provider_id,model,reasoning_effort,
                 started_at,status,estimate_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,'unconfirmed',?)""",
                (call_id, scene_id, episode_id, job_id, batch_id, role, purpose, provider_id, model,
                 reasoning_effort, self.clock(), json.dumps(estimate)))
            await self._db.commit()
        return call_id

    async def end_model_call(self, call_id, *, status, usage=None, output_estimate_tokens=None, error_type=None):
        if status not in {"completed", "failed", "cancelled"}:
            raise ValueError("Invalid model call completion status")
        async with self._write_lock:
            cursor = await self._db.execute("""UPDATE model_calls SET ended_at=?,status=?,usage_json=?,
                output_estimate_tokens=?,error_type=? WHERE id=? AND ended_at IS NULL""",
                (self.clock(), status, json.dumps(usage) if usage is not None else None,
                 output_estimate_tokens, error_type, call_id))
            if cursor.rowcount != 1:
                raise ValueError("Model call is missing or already ended")
            await self._db.commit()

    async def set_model_call_disposition(self, episode_id, disposition):
        if disposition not in {"silence", "expression", "rejected"}:
            raise ValueError("Invalid conversation disposition")
        async with self._write_lock:
            await self._db.execute("UPDATE model_calls SET disposition=? WHERE episode_id=? AND role='conversation'",
                                   (disposition, episode_id))
            await self._db.commit()

    async def list_model_calls(self, scene_id=None, limit=100):
        cursor = await self._db.execute("""SELECT * FROM model_calls WHERE (? IS NULL OR scene_id=?)
            ORDER BY started_at DESC,id DESC LIMIT ?""", (scene_id, scene_id, limit))
        names = [column[0] for column in cursor.description]
        result = []
        for row in await cursor.fetchall():
            item = dict(zip(names, row))
            usage = item.pop("usage_json")
            item["usage"] = json.loads(usage) if usage is not None else None
            item["estimate"] = json.loads(item.pop("estimate_json"))
            result.append(item)
        return result

    async def get_model_call_totals(self, scene_id=None):
        # Raw provider usage stays intact above. Derived totals preserve the
        # provider's output meaning; reasoning is never added to completion.
        cursor = await self._db.execute("""SELECT purpose,disposition,status,usage_json,estimate_json
            FROM model_calls WHERE (? IS NULL OR scene_id=?)""", (scene_id, scene_id))
        totals = {}
        for purpose, disposition, status, encoded_usage, encoded_estimate in await cursor.fetchall():
            item = totals.setdefault((purpose, disposition), {"purpose": purpose, "disposition": disposition,
                "calls": 0, "completed": 0, "failed": 0, "cancelled": 0, "unconfirmed": 0,
                "unknown_usage": 0, "prompt_tokens": 0, "completion_tokens": 0,
                "cached_tokens": 0, "reasoning_tokens": 0, "estimated_input_tokens": 0})
            item["calls"] += 1
            item[status] += 1
            usage = json.loads(encoded_usage) if encoded_usage is not None else None
            if not isinstance(usage, dict):
                usage = None
            item["estimated_input_tokens"] += json.loads(encoded_estimate)["input_tokens"]
            if not isinstance(usage, dict) or not isinstance(usage.get("prompt_tokens"), (int, float)) or not isinstance(usage.get("completion_tokens"), (int, float)):
                item["unknown_usage"] += 1
            if isinstance(usage, dict):
                for field in ("prompt_tokens", "completion_tokens"):
                    if isinstance(usage.get(field), (int, float)):
                        item[field] += usage[field]
                for field, details, key in (("cached_tokens", "prompt_tokens_details", "cached_tokens"),
                                             ("reasoning_tokens", "completion_tokens_details", "reasoning_tokens")):
                    detail = usage.get(details)
                    value = detail.get(key) if isinstance(detail, dict) else None
                    if isinstance(value, (int, float)):
                        item[field] += value
        return list(totals.values())
