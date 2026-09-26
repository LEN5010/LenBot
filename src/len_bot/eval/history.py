"""Small, read-only history corpus and human-scored baseline export.

The source is a named offline legacy SQLite backup, never the running database.
Candidate selection finds observable conversation shapes; it does not decide
whether an answer was good or whether silence was appropriate.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any


RECEIVED = {"GROUP_MESSAGE_RECEIVED", "PRIVATE_MESSAGE_RECEIVED"}
SENT = "MESSAGE_SENT"
OBSERVATION_SECONDS = 300
FOLLOWUP_SECONDS = 1800


@dataclass(frozen=True)
class _Message:
    rowid: int
    kind: str
    scene: str
    actor: str
    timestamp: float
    payload: dict[str, Any]
    metadata: dict[str, Any]


def _read_messages(source: Path) -> tuple[dict[str, list[_Message]], float, float, dict[str, int], dict[tuple[str, str], list[float]]]:
    uri = source.resolve().as_uri() + "?mode=ro&immutable=1"
    scenes: dict[str, list[_Message]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    signals: dict[tuple[str, str], list[float]] = defaultdict(list)
    with sqlite3.connect(uri, uri=True) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
        required = {"event_type", "scene_id", "actor_id", "timestamp", "payload", "metadata"}
        if not required <= columns:
            raise ValueError(f"events schema lacks {sorted(required - columns)}")
        source_start, source_end = db.execute("SELECT MIN(timestamp),MAX(timestamp) FROM events").fetchone()
        if source_end is None:
            raise ValueError("Backup contains no events")
        rows = db.execute(
            "SELECT rowid,event_type,scene_id,actor_id,timestamp,payload,metadata "
            "FROM events WHERE event_type IN "
            "('GROUP_MESSAGE_RECEIVED','PRIVATE_MESSAGE_RECEIVED','MESSAGE_SENT') "
            "ORDER BY timestamp,rowid"
        )
        for rowid, kind, scene, actor, timestamp, payload_raw, metadata_raw in rows:
            try:
                payload = json.loads(payload_raw)
                metadata = json.loads(metadata_raw)
                if kind == SENT:
                    live = payload["delivery_status"] == "sent" and payload["origin_mode"] == "live"
                    if not live or metadata["simulated"]:
                        counts["excluded_non_live_or_unconfirmed_sends"] += 1
                        continue
                    _ = payload["raw_text"]
                else:
                    _ = (payload["raw_text"], payload["at_bot"], payload["reply_bot"],
                         payload["message_id"], payload["reply_to_message_id"], payload["sender"])
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid event row {rowid}: {str(payload_raw)[:160]}") from exc
            scenes[scene].append(_Message(rowid, kind, scene, actor, float(timestamp), payload, metadata))
            counts["received" if kind in RECEIVED else "confirmed_live_sends"] += 1
        for scene, kind, timestamp in db.execute(
            "SELECT scene_id,event_type,timestamp FROM events "
            "WHERE event_type IN ('MESSAGE_SEND_FAILED','CONVERSATION_COMMITTED') "
            "ORDER BY timestamp,rowid"
        ):
            signals[(scene, kind)].append(float(timestamp))
            counts["failed_send_events" if kind == "MESSAGE_SEND_FAILED" else "conversation_commits"] += 1
    return scenes, float(source_start), float(source_end), dict(counts), signals


class _Aliases:
    def __init__(self, scenes: dict[str, list[_Message]]) -> None:
        self.scenes = {key: f"scene_{i:03d}" for i, key in enumerate(sorted(scenes), 1)}
        actors = sorted({event.actor for events in scenes.values() for event in events if event.kind in RECEIVED})
        self.actors = {key: f"person_{i:03d}" for i, key in enumerate(actors, 1)}
        message_ids = sorted({str(event.payload["message_id"]) for events in scenes.values()
                              for event in events if event.payload.get("message_id") is not None})
        self.message_ids = {key: f"message_{i:05d}" for i, key in enumerate(message_ids, 1)}
        names: dict[str, set[str]] = defaultdict(set)
        for events in scenes.values():
            for event in events:
                if event.kind in RECEIVED:
                    sender = event.payload["sender"]
                    for name in (sender["nickname"], sender["card"]):
                        if name and len(name) >= 2:
                            names[name].add(self.actors[event.actor])
        self.names = sorted(
            ((name, next(iter(actors)) if len(actors) == 1 else "[同名群友]")
             for name, actors in names.items()),
            key=lambda item: len(item[0]), reverse=True,
        )
        self.qq_aliases = {actor.removeprefix("user:"): alias for actor, alias in self.actors.items()
                           if actor.startswith("user:")}

    def text(self, raw: str) -> str:
        def replace_cq(match: re.Match[str]) -> str:
            kind, body = match.group(1), match.group(2)
            if kind == "at":
                qq = re.search(r"(?:^|,)qq=([^,]+)", body)
                return f"[@{self.qq_aliases.get(qq.group(1), 'unmapped_person')}]" if qq else "[@unmapped_person]"
            if kind == "reply":
                reply = re.search(r"(?:^|,)id=([^,]+)", body)
                return f"[reply:{self.message_ids.get(reply.group(1), 'unavailable_message')}]" if reply else "[reply:unavailable_message]"
            return f"[{kind}]"

        text = re.sub(r"\[CQ:([a-zA-Z_]+)([^\]]*)\]", replace_cq, raw)
        text = re.sub(r"https?://\S+", "[url]", text)
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
        for name, alias in self.names:
            text = text.replace(name, alias)
        for qq, alias in self.qq_aliases.items():
            text = re.sub(rf"(?<!\d){re.escape(qq)}(?!\d)", alias, text)
        return re.sub(r"(?<!\d)\d{5,}(?!\d)", "[number]", text)

    def render(self, event: _Message) -> dict[str, Any]:
        payload = event.payload
        result: dict[str, Any] = {
            "source_rowid": event.rowid,
            "time": _iso(event.timestamp),
            "kind": "human" if event.kind in RECEIVED else "bot_confirmed_sent",
            "actor": self.actors[event.actor] if event.kind in RECEIVED else "bot",
            "text": self.text(payload["raw_text"]),
            "message": self.message_ids.get(str(payload["message_id"]))
            if payload.get("message_id") is not None else None,
        }
        if event.kind in RECEIVED:
            reference = payload["reply_to_message_id"]
            result.update({
                "at_bot": payload["at_bot"], "reply_bot": payload["reply_bot"],
                "reply_to": self.message_ids.get(str(reference), "unavailable_message")
                if reference is not None else None,
            })
        return result


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def _event_count(times: list[float], start: float, end: float) -> int:
    return bisect_right(times, end) - bisect_right(times, start)


def _select_diverse(items: list[dict[str, Any]], limit: int, occupied: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        buckets[(item["scene_key"], _iso(item["time"])[:10])].append(item)
    for values in buckets.values():
        values.sort(key=lambda item: item["time"])
    selected = []
    existing = list(occupied or [])
    keys = sorted(buckets)
    while keys and len(selected) < limit:
        next_keys = []
        for key in keys:
            values = buckets[key]
            while values and any(other["scene_key"] == key[0] and abs(other["time"] - values[0]["time"]) < 900
                                 for other in existing):
                values.pop(0)
            if values:
                item = values.pop(0)
                selected.append(item)
                existing.append(item)
            if values:
                next_keys.append(key)
            if len(selected) == limit:
                break
        keys = next_keys
    return selected


def _context(events: list[_Message], start: int, end: int, aliases: _Aliases) -> dict[str, Any]:
    return {
        "messages": [aliases.render(event) for event in events[start:end]],
        "omitted_before": start > 0,
        "omitted_after": end < len(events),
    }


def _private_write(path: Path, content: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            fd = -1
            file.write(content)
    finally:
        if fd >= 0:
            os.close(fd)


def _private_directory(path: Path, *, owned_output: bool) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if owned_output:
        path.chmod(0o700)
    elif stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError(f"Output directory must be private (0700): {path}")


def extract_history_candidates(source: Path, output: Path) -> dict[str, Any]:
    """Extract bounded, diverse candidate windows from an explicit offline backup.

    ``output`` is a private directory. Neither candidate selection nor the
    annotation template constitutes a historical score.
    """
    source = Path(source)
    output = Path(output)
    if source.resolve() == Path("len_bot.db").resolve():
        raise ValueError("Refusing the production database path")
    scenes, source_start, source_end, counts, signals = _read_messages(source)
    aliases = _Aliases(scenes)
    coherence_pool: list[dict[str, Any]] = []
    timing_pool: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for scene, events in scenes.items():
        sent_indices = [i for i, event in enumerate(events) if event.kind == SENT]
        sent_times = [events[i].timestamp for i in sent_indices]
        for i, event in enumerate(events):
            if event.kind == SENT:
                followup = next((j for j in range(i + 1, len(events))
                                 if events[j].timestamp <= event.timestamp + FOLLOWUP_SECONDS
                                 and events[j].kind in RECEIVED), None)
                if followup is None:
                    continue
                later = bisect_right(sent_times, events[followup].timestamp)
                if later == len(sent_times) or sent_times[later] > event.timestamp + FOLLOWUP_SECONDS:
                    continue
                coherence_pool.append({
                    "scene_key": scene, "time": event.timestamp, "anchor": i,
                    "followup": followup, "response": sent_indices[later],
                    "strong": bool(events[followup].payload["at_bot"] or events[followup].payload["reply_bot"]),
                })
                continue
            if event.timestamp + OBSERVATION_SECONDS > source_end:
                continue
            next_sent = bisect_right(sent_times, event.timestamp)
            first_sent = sent_indices[next_sent] if next_sent < len(sent_indices) else None
            sent_5m = first_sent is not None and events[first_sent].timestamp <= event.timestamp + OBSERVATION_SECONDS
            direct = bool(event.payload["at_bot"] or event.payload["reply_bot"])
            stratum = ("direct" if direct else "ambient", "sent" if sent_5m else "no_sent")
            timing_pool[stratum].append({
                "scene_key": scene, "time": event.timestamp, "trigger": i,
                "first_sent": first_sent, "stratum": stratum,
            })

    strong = _select_diverse([item for item in coherence_pool if item["strong"]], 60)
    coherence = strong + _select_diverse(
        [item for item in coherence_pool if not item["strong"]], 100 - len(strong), strong
    )
    timing: list[dict[str, Any]] = []
    for stratum in (("direct", "sent"), ("direct", "no_sent"),
                    ("ambient", "sent"), ("ambient", "no_sent")):
        timing.extend(_select_diverse(timing_pool[stratum], 25, timing))
    candidates = []
    selected_timing_strata: dict[str, int] = defaultdict(int)
    for index, item in enumerate(sorted(coherence, key=lambda value: (value["time"], value["scene_key"])), 1):
        events = scenes[item["scene_key"]]
        anchor, followup, response = item["anchor"], item["followup"], item["response"]
        candidates.append({
            "id": f"coherence-{index:03d}", "set": "coherence", "review_status": "unreviewed",
            "scene": aliases.scenes[item["scene_key"]],
            "anchor_time": _iso(events[anchor].timestamp),
            "anchor_rowid": events[anchor].rowid,
            "followup_rowid": events[followup].rowid,
            "response_rowid": events[response].rowid,
            "selection_signal": "direct_followup" if item["strong"] else "nearby_followup",
            "context": _context(events, max(0, anchor - 5), min(len(events), response + 4), aliases),
            "unknowns": ["historical_model", "historical_configuration", "unseen_prior_context", "tool_results"],
        })
    for index, item in enumerate(sorted(timing, key=lambda value: (value["time"], value["scene_key"])), 1):
        events = scenes[item["scene_key"]]
        trigger = item["trigger"]
        first = item["first_sent"]
        horizon = events[trigger].timestamp + OBSERVATION_SECONDS
        later_horizon = events[trigger].timestamp + FOLLOWUP_SECONDS
        sent_in_window = first is not None and events[first].timestamp <= horizon
        selected_timing_strata["_".join(item["stratum"])] += 1
        end = trigger + 1
        while end < len(events) and events[end].timestamp <= horizon:
            end += 1
        context_end = min(end, trigger + 14)
        context = _context(events, max(0, trigger - 5), context_end, aliases)
        context["omitted_within_observation"] = end - context_end
        observed_sends = [aliases.render(event) for event in events[trigger + 1:end] if event.kind == SENT]
        candidates.append({
            "id": f"timing-{index:03d}", "set": "timing", "review_status": "unreviewed",
            "scene": aliases.scenes[item["scene_key"]],
            "trigger_time": _iso(events[trigger].timestamp), "trigger_rowid": events[trigger].rowid,
            "observation_end": _iso(horizon),
            "observed": "confirmed_send_within_5m" if sent_in_window else "no_confirmed_send_within_5m",
            "first_confirmed_send_rowid": events[first].rowid if sent_in_window else None,
            "later_confirmed_send_within_30m": bool(first is not None and not sent_in_window
                                                      and events[first].timestamp <= later_horizon),
            "later_confirmed_send_time": _iso(events[first].timestamp)
            if first is not None and not sent_in_window and events[first].timestamp <= later_horizon else None,
            "non_delivery_signals_within_30m": {
                "failed_send_events": _event_count(signals[(item["scene_key"], "MESSAGE_SEND_FAILED")],
                                                   events[trigger].timestamp, later_horizon),
                "conversation_commits": _event_count(signals[(item["scene_key"], "CONVERSATION_COMMITTED")],
                                                     events[trigger].timestamp, later_horizon),
            },
            "context": context,
            "observed_sends": observed_sends,
            "unknowns": ["should_speak", "unconfirmed_delivery", "historical_model",
                         "historical_configuration", "unseen_prior_context", "scene_enabled_or_online"],
        })
    _private_directory(output, owned_output=True)
    candidate_path = output / "candidates.jsonl"
    _private_write(candidate_path, "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in candidates))
    _private_write(output / "annotations.template.jsonl",
        "".join(json.dumps({"id": item["id"], "verdict": None, "expected_speak": None, "reason": None},
                           ensure_ascii=False) + "\n" for item in candidates)
    )
    timing_strata = {
        f"{direct}_{observed}": selected_timing_strata[f"{direct}_{observed}"]
        for direct in ("direct", "ambient") for observed in ("sent", "no_sent")
    }
    manifest = {
        "source": str(source), "source_first_event": _iso(source_start),
        "source_last_event": _iso(source_end), "source_scene_count": len(scenes),
        "candidate_scene_count": len({item["scene"] for item in candidates}),
        "source_counts": counts,
        "candidate_counts": {name: sum(item["set"] == name for item in candidates)
                             for name in ("coherence", "timing")},
        "candidate_strata_counts": {
            "coherence": {
                signal: sum(item["set"] == "coherence" and item["selection_signal"] == signal
                            for item in candidates)
                for signal in ("direct_followup", "nearby_followup")
            },
            "timing": timing_strata,
        },
        "unreviewed": len(candidates), "scored": 0,
        "selection": "Up to 100 observed bot-human-bot followups and 100 five-minute timing windows, "
                     "diversified by scene/day; direct and ambient, send and no-send timing strata.",
        "review_guide": {
            "coherence": "Judge the actual later bot reply against its own earlier words and human follow-up; "
                         "use uncertain when the excerpt is insufficient.",
            "timing": "Decide whether a reply was warranted from the visible context, then judge the observed "
                      "five-minute send/no-send. A later send may answer another message; do not score by count. "
                      "Use observed_sends when context is truncated. No confirmed send does not prove appropriate "
                      "silence: delivery and scene online/enabled state may be unknown.",
            "verdicts": ["pass", "fail", "uncertain"],
            "notes": "For pass/fail supply a concrete semantic reason; timing pass/fail requires expected_speak. "
                     "Uncertain timing cases may leave expected_speak null. "
                     "Text is locally pseudonymized, not guaranteed free of names or other private detail.",
        },
    }
    _private_write(output / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def report_history_baseline(source: Path, annotations: Path, output: Path) -> dict[str, Any]:
    """Report only explicit human verdicts; missing and uncertain remain separate."""
    source = Path(source)
    candidates = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line]
    manifest = json.loads((source.parent / "manifest.json").read_text(encoding="utf-8"))
    candidate_by_id = {item["id"]: item for item in candidates}
    if len(candidate_by_id) != len(candidates):
        raise ValueError("Duplicate candidate ID")
    labels: dict[str, dict[str, Any]] = {}
    for line in Path(annotations).read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        item = json.loads(line)
        case_id = item["id"]
        if case_id not in candidate_by_id or case_id in labels:
            raise ValueError(f"Unknown or duplicate annotation: {case_id}")
        verdict = item["verdict"]
        if verdict not in (None, "pass", "fail", "uncertain"):
            raise ValueError(f"Invalid verdict for {case_id}: {verdict}")
        if verdict is not None:
            if not isinstance(item["reason"], str) or not item["reason"].strip():
                raise ValueError(f"Annotation {case_id} needs a reason")
            if (candidate_by_id[case_id]["set"] == "timing" and verdict in ("pass", "fail")
                    and type(item["expected_speak"]) is not bool):
                raise ValueError(f"Timing annotation {case_id} needs expected_speak")
        labels[case_id] = item
    by_set = {}
    for set_name in ("coherence", "timing"):
        subset = [item for item in candidates if item["set"] == set_name]
        statuses = [labels.get(item["id"], {}).get("verdict") for item in subset]
        passed, failed, uncertain = (statuses.count(value) for value in ("pass", "fail", "uncertain"))
        scored = passed + failed
        by_set[set_name] = {
            "candidates": len(subset), "reviewed": scored + uncertain,
            "scored": scored, "pass": passed, "fail": failed, "uncertain": uncertain,
            "unreviewed": len(subset) - scored - uncertain,
            "pass_rate": passed / scored if scored else None,
        }
    report = {"sets": by_set, "coverage": {
        "source_first_event": manifest["source_first_event"],
        "source_last_event": manifest["source_last_event"],
        "source_scene_count": manifest["source_scene_count"],
        "candidate_scene_count": manifest["candidate_scene_count"],
        "candidate_strata_counts": manifest["candidate_strata_counts"],
    }, "historical_baseline_complete": all(
        result["candidates"] >= 50 and result["unreviewed"] == 0 and result["uncertain"] == 0
        for result in by_set.values()),
        "scope": "human-reviewed historical experience only; no same-model or controlled comparison"}
    output = Path(output)
    _private_directory(output.parent, owned_output=False)
    _private_write(output, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report
