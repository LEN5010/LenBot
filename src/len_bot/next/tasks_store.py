"""Actual work tasks, native events, calls and copied deliverables in Store."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Literal

from .messages import UploadResult
from .store import Store, encode


TaskStatus = Literal["queued", "running", "waiting_input", "done", "failed", "cancelled"]
TERMINAL = frozenset({"done", "failed", "cancelled"})


@dataclass(frozen=True, slots=True)
class Task:
    id: int
    scene: str
    requester: str
    goal: str
    deliverable: str
    context: str
    input: str
    status: TaskStatus
    created: float
    started: float | None
    ended: float | None
    container: str | None
    question: dict | None
    summary: str | None
    error: str | None
    account_browser: bool = False
    browser_active: bool = False
    browser_session: str | None = None


@dataclass(frozen=True, slots=True)
class TaskFile:
    id: int
    scene: str
    task_id: int
    name: str
    path: str
    size: int
    note: str | None
    created: float


def _task(row: sqlite3.Row) -> Task:
    values = dict(row)
    values["question"] = None if values["question"] is None else json.loads(values["question"])
    values["account_browser"] = bool(values["account_browser"])
    values["browser_active"] = bool(values["browser_active"])
    return Task(**values)


class TaskStore:
    """Use the conversation Store's one connection and clock, not another DB."""

    def __init__(self, store: Store):
        self.db = store.db
        self.now = store.now

    def create(self, scene: str, requester: str, goal: str, deliverable: str,
               context: str, input: str, *, account_browser: bool = False) -> Task:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO tasks(scene,requester,goal,deliverable,context,input,status,created,account_browser) "
                "VALUES (?,?,?,?,?,?,'queued',?,?)",
                (scene, requester, goal, deliverable, context, input, self.now(), int(account_browser)),
            )
        return self.get(scene, cursor.lastrowid)

    def browser_in_use(self) -> list[Task]:
        return [_task(row) for row in self.db.execute("SELECT * FROM tasks WHERE browser_active=1 ORDER BY id")]

    def browser_binding(self, scene: str, id: int, *, active: bool, session: str | None) -> None:
        with self.db:
            self.db.execute("UPDATE tasks SET browser_active=?,browser_session=? WHERE scene=? AND id=?",
                            (int(active), session, scene, id))

    def get(self, scene: str, id: int) -> Task:
        row = self.db.execute("SELECT * FROM tasks WHERE scene=? AND id=?", (scene, id)).fetchone()
        if row is None:
            raise ValueError(f"Scene {scene} has no task {id}")
        return _task(row)

    def list(self, scene: str, *, status: str = "active", offset: int = 0,
             limit: int = 20) -> list[Task]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("task list offset must be nonnegative and limit 1..100")
        if status == "all":
            clause, values = "", ()
        elif status == "active":
            clause, values = " AND status IN ('queued','running','waiting_input')", ()
        elif status in {"queued", "running", "waiting_input", "done", "failed", "cancelled"}:
            clause, values = " AND status=?", (status,)
        else:
            raise ValueError(f"unknown task status filter: {status!r}")
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE scene=?" + clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
            (scene, *values, limit, offset),
        ).fetchall()
        return [_task(row) for row in rows]

    def queued(self) -> list[Task]:
        rows = self.db.execute("SELECT * FROM tasks WHERE status='queued' ORDER BY id").fetchall()
        return [_task(row) for row in rows]

    def active(self) -> list[Task]:
        rows = self.db.execute(
            "SELECT * FROM tasks WHERE status IN ('running','waiting_input') ORDER BY id"
        ).fetchall()
        return [_task(row) for row in rows]

    def containers(self) -> list[Task]:
        rows = self.db.execute("SELECT * FROM tasks WHERE container IS NOT NULL ORDER BY id").fetchall()
        return [_task(row) for row in rows]

    def count_created(self, scene: str, requester: str, since: float) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM tasks WHERE scene=? AND requester=? AND created>=?",
            (scene, requester, since),
        ).fetchone()[0]

    def start(self, scene: str, id: int) -> Task:
        with self.db:
            updated = self.db.execute(
                "UPDATE tasks SET status='running',started=? "
                "WHERE scene=? AND id=? AND status='queued'",
                (self.now(), scene, id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Task {id} in {scene} is {self.get(scene, id).status}, not queued")
        return self.get(scene, id)

    def execution_requester(self, item: Task) -> str:
        """A continued run is authorized by its actual continue operator."""
        row = self.db.execute(
            "SELECT json_extract(body,'$.requester') FROM task_events "
            "WHERE scene=? AND task_id=? AND kind='input' AND json_extract(body,'$.mode')='continue' "
            "ORDER BY id DESC LIMIT 1", (item.scene, item.id),
        ).fetchone()
        return item.requester if row is None else row[0]

    def set_container(self, scene: str, id: int, container: str | None) -> Task:
        with self.db:
            updated = self.db.execute(
                "UPDATE tasks SET container=? WHERE scene=? AND id=?", (container, scene, id)
            )
            if updated.rowcount != 1:
                raise ValueError(f"Scene {scene} has no task {id}")
        return self.get(scene, id)

    def set_question(self, scene: str, id: int, question: dict | None) -> Task:
        if question is None:
            target, previous = "running", "waiting_input"
        else:
            target, previous = "waiting_input", "running"
        with self.db:
            updated = self.db.execute(
                "UPDATE tasks SET status=?,question=? WHERE scene=? AND id=? AND status=?",
                (target, None if question is None else encode(question), scene, id, previous),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Task {id} in {scene} is {self.get(scene, id).status}, not {previous}")
        return self.get(scene, id)

    def finish(self, scene: str, id: int, status: Literal["done", "failed", "cancelled"],
               summary: str | None, error: str | None) -> Task:
        previous = ("queued", "running", "waiting_input") if status != "done" else ("running",)
        placeholders = ",".join("?" for _ in previous)
        with self.db:
            updated = self.db.execute(
                f"UPDATE tasks SET status=?,ended=?,summary=?,error=? "
                f"WHERE scene=? AND id=? AND status IN ({placeholders})",
                (status, self.now(), summary, error, scene, id, *previous),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Task {id} in {scene} cannot finish from {self.get(scene, id).status}")
        return self.get(scene, id)

    def requeue(self, scene: str, id: int, input: str, *, requester: str) -> Task:
        with self.db:
            current = self.get(scene, id)
            if current.status not in TERMINAL or current.container is not None:
                raise ValueError(f"Task {id} in {scene} is not a cleaned terminal task")
            self.db.execute(
                "UPDATE tasks SET status='queued',input=?,started=NULL,ended=NULL,"
                "question=NULL,summary=NULL,error=NULL WHERE scene=? AND id=?",
                (input, scene, id),
            )
            self.db.execute(
                "INSERT INTO task_events(scene,task_id,kind,body,created) VALUES (?,?,'input',?,?)",
                (scene, id, encode({"requester": requester, "text": input, "mode": "continue"}), self.now()),
            )
        return self.get(scene, id)

    def add_event(self, scene: str, id: int, kind: str, body: dict,
                  notice: str | None = None) -> int:
        with self.db:
            self.get(scene, id)
            cursor = self.db.execute(
                "INSERT INTO task_events(scene,task_id,kind,body,notice,created) "
                "VALUES (?,?,?,?,?,?)",
                (scene, id, kind, encode(body), notice, self.now()),
            )
        return cursor.lastrowid

    def event_previews(self, scene: str, id: int, *, after: int = 0,
                       limit: int = 100) -> list[dict]:
        self.get(scene, id)
        rows = self.db.execute(
            "SELECT id,kind,created,delivered_at,"
            "CASE WHEN kind='native' THEN json_extract(body,'$.type') ELSE kind END AS event_type,"
            "CASE WHEN kind='native' THEN json_extract(body,'$.toolName') END AS tool_name,"
            "CASE WHEN kind='native' THEN json_extract(body,'$.args.method') END AS browser_method,"
            "substr(body,1,1200) AS preview,length(body)>1200 AS truncated "
            "FROM task_events WHERE scene=? AND task_id=? AND id>? ORDER BY id LIMIT ?",
            (scene, id, after, limit),
        ).fetchall()
        return [{**dict(row), "truncated": bool(row["truncated"])} for row in rows]

    def event(self, scene: str, id: int, event_id: int) -> dict:
        row = self.db.execute(
            "SELECT * FROM task_events WHERE scene=? AND task_id=? AND id=?",
            (scene, id, event_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Scene {scene} task {id} has no event {event_id}")
        return {**dict(row), "body": json.loads(row["body"])}

    def pending_notices(self, scene: str) -> list[tuple[int, str]]:
        return [(row[0], row[1]) for row in self.db.execute(
            "SELECT id,notice FROM task_events WHERE scene=? "
            "AND notice IS NOT NULL AND delivered_at IS NULL ORDER BY id", (scene,),
        )]

    def start_call(self, scene: str, id: int, request: dict) -> int:
        return self.add_event(scene, id, "model_call", {"request": request, "response": None})

    def finish_call(self, eventid: int, facts: dict) -> None:
        with self.db:
            row = self.db.execute(
                "SELECT kind,body FROM task_events WHERE id=?", (eventid,)
            ).fetchone()
            if row is None or row["kind"] != "model_call":
                raise ValueError(f"No model call event {eventid}")
            body = json.loads(row["body"])
            if body["response"] is not None:
                raise ValueError(f"Model call event {eventid} is already finished")
            body["response"] = facts
            body["ended"] = self.now()
            self.db.execute("UPDATE task_events SET body=? WHERE id=?", (encode(body), eventid))

    def call_costs(self, scene: str, id: int) -> list[dict | None]:
        self.get(scene, id)
        rows = self.db.execute(
            "SELECT json_extract(body,'$.response.cost') FROM task_events WHERE scene=? AND task_id=? "
            "AND kind='model_call' ORDER BY id", (scene, id),
        ).fetchall()
        return [None if row[0] is None else json.loads(row[0]) for row in rows]

    def add_file(self, scene: str, id: int, *, name: str, path: str,
                 size: int, note: str | None) -> TaskFile:
        with self.db:
            self.get(scene, id)
            cursor = self.db.execute(
                "INSERT INTO task_files(scene,task_id,name,path,size,note,created) "
                "VALUES (?,?,?,?,?,?,?)",
                (scene, id, name, path, size, note, self.now()),
            )
        return self.get_file(scene, id, cursor.lastrowid)

    def get_file(self, scene: str, id: int, file_id: int) -> TaskFile:
        row = self.db.execute(
            "SELECT * FROM task_files WHERE scene=? AND task_id=? AND id=?",
            (scene, id, file_id),
        ).fetchone()
        if row is None:
            raise ValueError(f"Scene {scene} task {id} has no file {file_id}")
        return TaskFile(**dict(row))

    def list_files(self, scene: str, id: int) -> list[TaskFile]:
        self.get(scene, id)
        rows = self.db.execute(
            "SELECT * FROM task_files WHERE scene=? AND task_id=? ORDER BY id", (scene, id)
        ).fetchall()
        return [TaskFile(**dict(row)) for row in rows]

    def start_file_upload(self, file: TaskFile, platform_path: str) -> int:
        """Persist the actual upload attempt before the platform call begins."""
        return self.add_event(file.scene, file.task_id, "file_upload", {
            "file_id": file.id,
            "name": file.name,
            "size": file.size,
            "platform_path": platform_path,
            "status": "unconfirmed",
            "error": "尚未记录可靠平台回执",
            "platform_file_id": None,
            "raw": None,
            "ended": None,
        })

    def finish_file_upload(self, scene: str, task_id: int, event_id: int,
                           result: UploadResult) -> dict:
        """Replace the pending facts in the same native event, not a second event."""
        with self.db:
            row = self.db.execute(
                "SELECT kind,body,created FROM task_events WHERE scene=? AND task_id=? AND id=?",
                (scene, task_id, event_id),
            ).fetchone()
            if row is None or row["kind"] != "file_upload":
                raise ValueError(f"Scene {scene} task {task_id} has no file upload event {event_id}")
            body = json.loads(row["body"])
            if body["ended"] is not None:
                raise ValueError(f"File upload event {event_id} is already finished")
            body.update(status=result.status, error=result.error,
                        platform_file_id=result.file_id, raw=result.raw, ended=self.now())
            self.db.execute(
                "UPDATE task_events SET body=? WHERE scene=? AND task_id=? AND id=?",
                (encode(body), scene, task_id, event_id),
            )
        return {"created": row["created"], **body}

    def latest_file_upload(self, file: TaskFile) -> dict | None:
        row = self.db.execute(
            "SELECT created,body FROM task_events WHERE scene=? AND task_id=? AND kind='file_upload' "
            "AND json_extract(body,'$.file_id')=? ORDER BY id DESC LIMIT 1",
            (file.scene, file.task_id, file.id),
        ).fetchone()
        return None if row is None else {"created": row["created"], **json.loads(row["body"])}

    def start_egress_connection(self, scene: str, task_id: int, body: dict) -> int:
        return self.add_event(scene, task_id, "egress_connection", body)

    def update_egress_connection(self, scene: str, task_id: int, event_id: int,
                                 body: dict) -> None:
        with self.db:
            updated = self.db.execute(
                "UPDATE task_events SET body=? WHERE scene=? AND task_id=? AND id=? "
                "AND kind='egress_connection' "
                "AND json_extract(body,'$.status') IN ('opening','connected')",
                (encode(body), scene, task_id, event_id),
            )
            if updated.rowcount != 1:
                raise ValueError(f"Scene {scene} task {task_id} has no active egress event {event_id}")

    def recover_egress(self) -> int:
        """Mark only old unfinished connection rows; retain their last saved bytes."""
        with self.db:
            rows = self.db.execute(
                "SELECT id,body FROM task_events WHERE kind='egress_connection' "
                "AND json_extract(body,'$.status') IN ('opening','connected') ORDER BY id"
            ).fetchall()
            for row in rows:
                body = json.loads(row["body"])
                body["status"] = "interrupted"
                body["recovered_at"] = self.now()
                self.db.execute("UPDATE task_events SET body=? WHERE id=?", (encode(body), row["id"]))
        return len(rows)

    def egress_task_totals(self, scene: str, task_id: int) -> tuple[int, int]:
        self.get(scene, task_id)
        row = self.db.execute(
            "SELECT COALESCE(SUM(json_extract(body,'$.up')),0), "
            "COALESCE(SUM(json_extract(body,'$.down')),0) "
            "FROM task_events WHERE scene=? AND task_id=? AND kind='egress_connection'",
            (scene, task_id),
        ).fetchone()
        return int(row[0]), int(row[1])

    def egress_scene_day_totals(self, scene: str, day: str) -> tuple[int, int]:
        row = self.db.execute(
            "SELECT COALESCE(SUM(json_extract(day.value,'$.up')),0), "
            "COALESCE(SUM(json_extract(day.value,'$.down')),0) "
            "FROM task_events AS event, json_each(event.body,'$.days') AS day "
            "WHERE event.scene=? AND event.kind='egress_connection' AND day.key=?",
            (scene, day),
        ).fetchone()
        return int(row[0]), int(row[1])

    def egress_incomplete_count(self, scene: str, task_id: int | None = None) -> int:
        if task_id is not None:
            self.get(scene, task_id)
        row = self.db.execute(
            "SELECT COUNT(*) FROM task_events WHERE scene=? AND kind='egress_connection' "
            "AND (? IS NULL OR task_id=?) "
            "AND json_extract(body,'$.status')='interrupted'",
            (scene, task_id, task_id),
        ).fetchone()
        return int(row[0])

    def egress_last_error(self, scene: str, task_id: int | None = None) -> dict | None:
        row = self.db.execute(
            "SELECT json_extract(body,'$.ended'),json_extract(body,'$.error') "
            "FROM task_events WHERE scene=? AND kind='egress_connection' "
            "AND (? IS NULL OR task_id=?) AND json_extract(body,'$.error') IS NOT NULL "
            "ORDER BY json_extract(body,'$.ended') DESC,id DESC LIMIT 1", (scene, task_id, task_id),
        ).fetchone()
        return None if row is None else {"at": row[0], "message": row[1]}
