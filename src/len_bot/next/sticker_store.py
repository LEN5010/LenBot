"""Scene-local collected sticker candidates, immutable originals, and actual calls."""

from __future__ import annotations

import json
from typing import Literal

from .sticker_assets import CollectedSticker
from .store import Store, encode


CALL_SUMMARY = (
    "id,scene,candidate_id,source_message_seq,image_index,started,ended,status,model_started,usage,cost,error"
)
CANDIDATE_SELECT = (
    "SELECT c.*,m.mime_type,m.width,m.height,m.animated,length(m.data) AS bytes "
    "FROM sticker_candidates c LEFT JOIN media m ON m.id=c.media_id "
)
ORIGINAL_NOTICE = "群友原图（暂无描述）"


class StickerStore:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db

    @staticmethod
    def _candidate(row) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        media_type = value.pop("mime_type")
        width = value.pop("width")
        height = value.pop("height")
        animated = value.pop("animated")
        byte_count = value.pop("bytes")
        value["meta"] = (None if media_type is None else
                         {"mime_type": media_type, "width": width, "height": height,
                          "animated": bool(animated), "bytes": byte_count})
        value["emotions"] = json.loads(value["emotions"])
        value["tags"] = json.loads(value["tags"])
        value["is_sticker"] = None if value["is_sticker"] is None else bool(value["is_sticker"])
        if "uses" in value:
            value["uses"] = int(value["uses"])
        return value

    @staticmethod
    def _call(row) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        for field in ("request", "response", "usage", "cost"):
            if field in value and value[field] is not None:
                value[field] = json.loads(value[field])
        return value

    def recover(self, scene: str) -> None:
        with self.db:
            now = self.store.now()
            error = "Previous sticker processing was interrupted; not replayed"
            self.db.execute(
                "UPDATE sticker_candidates SET status='interrupted',updated=?,error=? "
                "WHERE scene=? AND status='running'", (now, error, scene),
            )
            self.db.execute(
                "UPDATE sticker_calls SET status='interrupted',ended=?,error=? "
                "WHERE scene=? AND status='running'", (now, error, scene),
            )

    def counts(self, scene: str) -> dict[str, int]:
        return {"queued": 0, "running": 0, "complete": 0, "failed": 0, "interrupted": 0,
                **dict(self.db.execute(
                    "SELECT status,COUNT(*) FROM sticker_candidates WHERE scene=? GROUP BY status", (scene,),
                ))}

    def next_queued(self, scene: str) -> dict | None:
        return self._candidate(self.db.execute(
            CANDIDATE_SELECT +
            "WHERE c.scene=? AND c.status='queued' AND c.review!='rejected' ORDER BY c.id LIMIT 1",
            (scene,),
        ).fetchone())

    def item(self, scene: str, id: int) -> dict | None:
        return self._candidate(self.db.execute(
            CANDIDATE_SELECT + "WHERE c.scene=? AND c.id=?", (scene, id),
        ).fetchone())

    def items(self, scene: str, review: str | None, status: str | None,
              limit: int, offset: int) -> dict:
        conditions = ["c.scene=?"]
        values: list[object] = [scene]
        if review is not None:
            conditions.append("c.review=?")
            values.append(review)
        if status is not None:
            conditions.append("c.status=?")
            values.append(status)
        clause = " AND ".join(conditions)
        rows = self.db.execute(
            CANDIDATE_SELECT + "WHERE " + clause + " ORDER BY c.id DESC LIMIT ? OFFSET ?",
            (*values, limit, offset),
        )
        total = self.db.execute(
            "SELECT COUNT(*) FROM sticker_candidates c WHERE " + clause, values,
        ).fetchone()[0]
        return {"items": [self._candidate(row) for row in rows], "total": total,
                "limit": limit, "offset": offset}

    def begin(self, scene: str, id: int) -> int:
        with self.db:
            now = self.store.now()
            updated = self.db.execute(
                "UPDATE sticker_candidates SET status='running',updated=?,error=NULL "
                "WHERE scene=? AND id=? AND status='queued' AND review!='rejected'",
                (now, scene, id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"No queued sticker candidate {id} in {scene}")
            cursor = self.db.execute(
                "INSERT INTO sticker_calls(scene,candidate_id,source_message_seq,image_index,started,status) "
                "SELECT scene,id,source_message_seq,image_index,?,'running' FROM sticker_candidates "
                "WHERE scene=? AND id=?", (now, scene, id),
            )
        return cursor.lastrowid

    def save_original(self, scene: str, id: int, *, data: bytes, mime_type: str,
                      width: int, height: int, animated: bool) -> None:
        with self.db:
            candidate = self.db.execute(
                "SELECT source_message_seq,image_index,media_id FROM sticker_candidates "
                "WHERE scene=? AND id=? AND status='running'", (scene, id),
            ).fetchone()
            if candidate is None:
                raise ValueError(f"No running sticker candidate {id} in {scene}")
            if candidate["media_id"] is not None:
                raise ValueError(f"Sticker candidate {id} already has an immutable original")
            media_id = self.db.execute(
                "INSERT INTO media(persona_id,file,source_message_seq,source_image_index,"
                "mime_type,width,height,animated,data) VALUES (NULL,NULL,?,?,?,?,?,?,?)",
                (candidate["source_message_seq"], candidate["image_index"], mime_type,
                 width, height, int(animated), data),
            ).lastrowid
            self.db.execute(
                "UPDATE sticker_candidates SET media_id=?,updated=? WHERE scene=? AND id=?",
                (media_id, self.store.now(), scene, id),
            )
            self.db.execute(
                "INSERT INTO message_media(message_seq,image_index,media_id,description,emotions,tags) "
                "VALUES (?,?,?,?,?,?)",
                (candidate["source_message_seq"], candidate["image_index"], media_id,
                 ORIGINAL_NOTICE, "[]", "[]"),
            )

    def original(self, scene: str, id: int) -> tuple[str, bytes] | None:
        row = self.db.execute(
            "SELECT m.mime_type,m.data FROM sticker_candidates c "
            "JOIN media m ON m.id=c.media_id WHERE c.scene=? AND c.id=?", (scene, id),
        ).fetchone()
        return None if row is None else (row[0], row[1])

    def set_request(self, callid: int, request: dict) -> None:
        with self.db:
            self.db.execute("UPDATE sticker_calls SET request=? WHERE id=?", (encode(request), callid))

    def mark_model_started(self, callid: int) -> None:
        with self.db:
            self.db.execute("UPDATE sticker_calls SET model_started=? WHERE id=?",
                            (self.store.now(), callid))

    def response(self, callid: int, response: object | None,
                 usage: dict | None, cost: dict | None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE sticker_calls SET response=?,usage=?,cost=? WHERE id=?",
                (None if response is None else encode(response),
                 None if usage is None else encode(usage),
                 None if cost is None else encode(cost), callid),
            )

    def complete(self, callid: int, *, description: str, text: str,
                 emotions: list[str], tags: list[str], is_sticker: bool) -> None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            candidate = self.db.execute(
                "SELECT c.id,c.scene,c.source_message_seq,c.image_index,c.media_id "
                "FROM sticker_calls a JOIN sticker_candidates c ON c.id=a.candidate_id "
                "AND c.scene=a.scene WHERE a.id=? AND a.status='running' AND c.status='running'",
                (callid,),
            ).fetchone()
            if candidate is None or candidate["media_id"] is None:
                raise ValueError(f"Sticker call {callid} has no running candidate with an original")
            now = self.store.now()
            self.db.execute(
                "UPDATE sticker_candidates SET status='complete',description=?,text=?,emotions=?,"
                "tags=?,is_sticker=?,updated=?,error=NULL WHERE id=?",
                (description, text, encode(emotions), encode(tags), int(is_sticker), now, candidate["id"]),
            )
            self.db.execute(
                "UPDATE message_media SET description=?,emotions=?,tags=? "
                "WHERE message_seq=? AND image_index=? AND media_id=?",
                (description, encode(emotions), encode(tags), candidate["source_message_seq"],
                 candidate["image_index"], candidate["media_id"]),
            )
            self.db.execute("UPDATE sticker_calls SET status='complete',ended=? WHERE id=?", (now, callid))

    def fail(self, callid: int, status: Literal["failed", "interrupted"], error: str) -> None:
        with self.db:
            call = self.db.execute(
                "SELECT scene,candidate_id FROM sticker_calls WHERE id=? AND status='running'", (callid,),
            ).fetchone()
            if call is None:
                raise ValueError(f"No running sticker call {callid}")
            now = self.store.now()
            self.db.execute(
                "UPDATE sticker_calls SET status=?,ended=?,error=? WHERE id=?",
                (status, now, error, callid),
            )
            self.db.execute(
                "UPDATE sticker_candidates SET status=?,updated=?,error=? WHERE scene=? AND id=?",
                (status, now, error, call["scene"], call["candidate_id"]),
            )

    def retry(self, scene: str, id: int) -> dict:
        with self.db:
            changed = self.db.execute(
                "UPDATE sticker_candidates SET status='queued',updated=?,error=NULL "
                "WHERE scene=? AND id=? AND status IN ('failed','interrupted') AND review!='rejected'",
                (self.store.now(), scene, id),
            )
            if changed.rowcount != 1:
                raise ValueError(f"No failed or interrupted sticker candidate {id} in {scene}")
        return self.item(scene, id)

    def update(self, scene: str, id: int, *, description: str | None, text: str | None,
               emotions: list[str], tags: list[str], review: str) -> dict | None:
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            current = self.db.execute(
                "SELECT source_message_seq,image_index,media_id,status,description,text,emotions,tags "
                "FROM sticker_candidates "
                "WHERE scene=? AND id=?", (scene, id),
            ).fetchone()
            if current is None:
                return None
            if (current["status"] != "complete" and
                    (description != current["description"] or text != current["text"]
                     or emotions != json.loads(current["emotions"])
                     or tags != json.loads(current["tags"]))):
                raise ValueError("等待标注完成后再编辑")
            if review == "adopted" and (current["status"] != "complete" or current["media_id"] is None
                                        or description is None or not description.strip()):
                raise ValueError("Adopted sticker requires a complete original and nonblank description")
            now = self.store.now()
            self.db.execute(
                "UPDATE sticker_candidates SET description=?,text=?,emotions=?,tags=?,review=?,updated=? "
                "WHERE scene=? AND id=?",
                (description, text, encode(emotions), encode(tags), review, now, scene, id),
            )
            if current["media_id"] is not None:
                self.db.execute(
                    "UPDATE message_media SET description=?,emotions=?,tags=? "
                    "WHERE message_seq=? AND image_index=? AND media_id=?",
                    (ORIGINAL_NOTICE if description is None else description,
                     encode(emotions), encode(tags), current["source_message_seq"],
                     current["image_index"], current["media_id"]),
                )
        return self.item(scene, id)

    def delete(self, scene: str, id: int) -> bool:
        with self.db:
            current = self.db.execute(
                "SELECT status FROM sticker_candidates WHERE scene=? AND id=?", (scene, id),
            ).fetchone()
            if current is None:
                return False
            if current["status"] == "running":
                raise ValueError(f"Sticker candidate {id} is running, not deletable")
            self.db.execute("DELETE FROM sticker_candidates WHERE scene=? AND id=?", (scene, id))
        return True

    def matches(self, scene: str, *, emotion: str | None, query: str | None) -> list[dict]:
        rows = self.db.execute(
            CANDIDATE_SELECT +
            "WHERE c.scene=? AND c.review='adopted' AND c.status='complete' "
            "AND c.media_id IS NOT NULL ORDER BY c.id", (scene,),
        )
        needle = (emotion if emotion is not None else query).casefold()
        matched = []
        for row in rows:
            item = self._candidate(row)
            values = (item["emotions"] if emotion is not None else
                      [item["description"], *item["emotions"], *item["tags"]])
            if ((any(value.casefold() == needle for value in values) if emotion is not None else
                 any(needle in value.casefold() for value in values))):
                matched.append(item)
        if matched:
            uses = dict(self.db.execute(
                "SELECT mm.media_id,COUNT(*) FROM message_media mm "
                "JOIN messages msg ON msg.seq=mm.message_seq "
                "WHERE mm.media_id IN (SELECT value FROM json_each(?)) AND msg.scene=? "
                "AND json_extract(msg.body,'$.send_status')='sent' GROUP BY mm.media_id",
                (encode([item["media_id"] for item in matched]), scene),
            ))
            for item in matched:
                item["uses"] = uses.get(item["media_id"], 0)
        return matched

    def asset(self, scene: str, id: int) -> CollectedSticker:
        row = self.db.execute(
            "SELECT c.id,c.media_id,c.source_message_seq,c.image_index,c.description,"
            "c.emotions,c.tags,m.data FROM sticker_candidates c "
            "JOIN media m ON m.id=c.media_id WHERE c.scene=? AND c.id=? "
            "AND c.review='adopted' AND c.status='complete'", (scene, id),
        ).fetchone()
        if row is None:
            raise ValueError(f"No adopted collected sticker {id} in {scene}")
        return CollectedSticker(
            candidate_id=row["id"], media_id=row["media_id"],
            source_message_seq=row["source_message_seq"], source_image_index=row["image_index"],
            description=row["description"], emotions=tuple(json.loads(row["emotions"])),
            tags=tuple(json.loads(row["tags"])), data=row["data"],
        )

    def calls(self, scene: str, limit: int, offset: int) -> dict:
        rows = self.db.execute(
            f"SELECT {CALL_SUMMARY} FROM sticker_calls WHERE scene=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (scene, limit, offset),
        )
        total = self.db.execute("SELECT COUNT(*) FROM sticker_calls WHERE scene=?", (scene,)).fetchone()[0]
        return {"items": [self._call(row) for row in rows], "total": total,
                "limit": limit, "offset": offset}

    def call(self, scene: str, id: int) -> dict | None:
        return self._call(self.db.execute(
            "SELECT * FROM sticker_calls WHERE scene=? AND id=?", (scene, id),
        ).fetchone())
