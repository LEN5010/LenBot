"""Observed post-delivery context, kept separate from cognition and human grading."""
import json

from len_bot.events.models import EventType


class ReplyFeedbackStoreMixin:
    async def initialize_reply_feedback(self):
        await self._db.executescript("""
            CREATE TABLE IF NOT EXISTS reply_observations (
                sent_event_id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, message_id TEXT NOT NULL,
                started_at REAL NOT NULL, expires_at REAL NOT NULL, human_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS reply_observations_scene ON reply_observations(scene_id, expires_at);
            CREATE TABLE IF NOT EXISTS reply_followups (
                sent_event_id TEXT NOT NULL, human_event_id TEXT NOT NULL, quoted INTEGER NOT NULL,
                PRIMARY KEY(sent_event_id,human_event_id)
            );
            CREATE TABLE IF NOT EXISTS reply_feedback_labels (
                id TEXT PRIMARY KEY, scene_id TEXT NOT NULL, sent_event_id TEXT NOT NULL,
                human_event_id TEXT, kind TEXT NOT NULL, acceptable INTEGER,
                comment TEXT NOT NULL, reviewer TEXT NOT NULL, created_at REAL NOT NULL
            );
        """)

    async def observe_reply_in_transaction(self, event):
        # Import and replay sources are never evidence of a real audience reaction.
        if any(event.metadata.get(key) for key in ("simulated", "replay_historical", "replay_input", "replay_injected")):
            return
        if event.event_type == EventType.MESSAGE_SENT:
            message_id = event.payload.get("message_id")
            if message_id is not None and event.payload.get("origin_mode", "live") == "live":
                await self._db.execute("INSERT INTO reply_observations VALUES(?,?,?,?,?,0)",
                    (event.id, event.scene_id, str(message_id), self.clock(), self.clock()+300))
        elif event.event_type in {EventType.GROUP_MESSAGE_RECEIVED, EventType.PRIVATE_MESSAGE_RECEIVED}:
            rows = await (await self._db.execute(
                "SELECT sent_event_id,message_id FROM reply_observations WHERE scene_id=? AND expires_at>? "
                "AND started_at<=? AND human_count<15", (event.scene_id, self.clock(), event.timestamp))).fetchall()
            for sent_id, message_id in rows:
                await self._db.execute("INSERT INTO reply_followups VALUES(?,?,?)",
                    (sent_id, event.id, int(str(event.payload.get("reply_to_message_id")) == message_id)))
                await self._db.execute("UPDATE reply_observations SET human_count=human_count+1 WHERE sent_event_id=?", (sent_id,))
        elif event.event_type == EventType.REPLY_FEEDBACK_LABELLED:
            data = event.payload
            target = await (await self._db.execute(
                "SELECT 1 FROM reply_observations WHERE sent_event_id=? AND scene_id=?",
                (data["sent_event_id"], event.scene_id))).fetchone()
            if not target:
                raise ValueError("反馈目标必须是本场景真实发送回执")
            human_id = data.get("human_event_id")
            if human_id and not await (await self._db.execute(
                "SELECT 1 FROM reply_followups WHERE sent_event_id=? AND human_event_id=?",
                (data["sent_event_id"], human_id))).fetchone():
                raise ValueError("人类反馈不在该回复的观察窗口内")
            if data["kind"] not in {"naturalness", "followup", "correction", "positive", "negative", "unrelated", "unknown"}:
                raise ValueError("未知反馈分类")
            if data["kind"] not in {"naturalness", "unknown"} and not human_id:
                raise ValueError("互动分类需要引用具体人类消息")
            await self._db.execute("INSERT INTO reply_feedback_labels VALUES(?,?,?,?,?,?,?,?,?)",
                (event.id, event.scene_id, data["sent_event_id"], human_id, data["kind"],
                 data.get("acceptable"), data.get("comment", ""), event.actor_id, self.clock()))

    async def reply_feedback(self, scene_id, limit=100):
        rows = await (await self._db.execute(
            "SELECT r.*,e.payload FROM reply_observations r JOIN events e ON e.id=r.sent_event_id "
            "WHERE r.scene_id=? ORDER BY r.started_at DESC LIMIT ?", (scene_id, min(max(limit,1),200)))).fetchall()
        result = []
        for sent_id, scope, message_id, started, expires, count, raw in rows:
            followups = await (await self._db.execute(
                "SELECT f.human_event_id,f.quoted,e.actor_id,e.payload FROM reply_followups f JOIN events e "
                "ON e.id=f.human_event_id WHERE f.sent_event_id=? AND e.scene_id=? ORDER BY e.rowid", (sent_id, scope))).fetchall()
            labels = await (await self._db.execute(
                "SELECT human_event_id,kind,acceptable,comment,reviewer,created_at FROM reply_feedback_labels "
                "WHERE sent_event_id=? AND scene_id=? ORDER BY rowid", (sent_id, scope))).fetchall()
            result.append(dict(sent_event_id=sent_id, scene_id=scope, message_id=message_id, sent=json.loads(raw),
                started_at=started, expires_at=expires, human_count=count,
                window_closed=count>=15 or self.clock()>=expires,
                response_status="observed" if any(row[1] for row in followups) or any(row[1] in {"followup","correction","positive","negative"} for row in labels) else "unknown",
                followups=[dict(event_id=row[0], quoted=bool(row[1]), actor_id=row[2], payload=json.loads(row[3])) for row in followups],
                labels=[dict(zip(("human_event_id","kind","acceptable","comment","reviewer","created_at"), row)) for row in labels]))
        return result
