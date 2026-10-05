"""Actual work tasks, native events, calls and copied deliverables in Store."""

from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from typing import Literal, TYPE_CHECKING
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..platform.messages import UploadResult
from ..storage.codec import encode
from .materials import MATERIALS
from ..memory.embeddings import _reject_constant

if TYPE_CHECKING:
    from ..storage.store import Store


TaskStatus = Literal["queued", "running", "waiting_input", "done", "failed", "cancelled"]
TERMINAL = frozenset({"done", "failed", "cancelled"})


SCHEMA = """
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL, requester TEXT NOT NULL,
    goal TEXT NOT NULL, deliverable TEXT NOT NULL, context TEXT NOT NULL,
    input TEXT NOT NULL, materials TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK(status IN
        ('queued','running','waiting_input','done','failed','cancelled')),
    created REAL NOT NULL, started REAL, ended REAL,
    container TEXT, question TEXT, summary TEXT, error TEXT,
    account_browser INTEGER NOT NULL DEFAULT 0 CHECK(account_browser IN (0,1)),
    browser_active INTEGER NOT NULL DEFAULT 0 CHECK(browser_active IN (0,1)),
    browser_session TEXT
);
CREATE INDEX tasks_scene_status ON tasks(scene,status,id);
CREATE INDEX tasks_status ON tasks(status,id);
CREATE INDEX tasks_containers ON tasks(id) WHERE container IS NOT NULL;
CREATE INDEX tasks_requester_created ON tasks(scene,requester,created);
CREATE TABLE task_events (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL, task_id INTEGER NOT NULL,
    kind TEXT NOT NULL, body TEXT NOT NULL, notice TEXT,
    created REAL NOT NULL, delivered_at REAL
);
CREATE INDEX task_events_task ON task_events(scene,task_id,id);
CREATE INDEX task_events_pending_notice ON task_events(scene,id)
    WHERE notice IS NOT NULL AND delivered_at IS NULL;
CREATE TABLE task_files (
    id INTEGER PRIMARY KEY, scene TEXT NOT NULL, task_id INTEGER NOT NULL,
    name TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL,
    note TEXT, created REAL NOT NULL
);
CREATE INDEX task_files_task ON task_files(scene,task_id,id);
CREATE INDEX task_model_usage ON task_events(scene,created) WHERE kind='model_call';
"""


class TaskInput(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    mode: Literal['continue', 'steer']
    requester: str = Field(pattern=r'^[a-z][a-z0-9_-]*:[^:\s/\\]+$')
    text: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class Task:
    id: int
    scene: str
    requester: str
    goal: str
    deliverable: str
    context: str
    input: str
    materials: list[str]
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
    raw_materials = values['materials']
    try:
        values['materials'] = MATERIALS.validate_json(raw_materials, strict=True)
    except ValidationError as error:
        raise ValueError(f'Invalid task material selection: {error}; raw={raw_materials[:1000]!r}') from error
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
               context: str, input: str, *, account_browser: bool = False, materials: tuple[str, ...] = ()) -> Task:
        with self.db:
            cursor = self.db.execute(
                "INSERT INTO tasks(scene,requester,goal,deliverable,context,input,materials,status,created,account_browser) "
                "VALUES (?,?,?,?,?,?,?,'queued',?,?)",
                (scene, requester, goal, deliverable, context, input, encode(list(materials)), self.now(), int(account_browser)),
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

    def workspace_discarded(self, scene: str, id: int) -> bool:
        return self.db.execute("SELECT 1 FROM task_events WHERE scene=? AND task_id=? AND kind='workspace_discard' LIMIT 1",
                               (scene, id)).fetchone() is not None

    def registered_files(self) -> list[TaskFile]:
        return [TaskFile(**dict(row)) for row in self.db.execute('SELECT * FROM task_files ORDER BY id')]

    def list(self, scene: str, *, status: str = "active", offset: int = 0,
             limit: int = 20, since: float | None = None, before: float | None = None,
             environment: Literal['all', 'retained', 'discarded'] = 'all') -> list[Task]:
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("task list offset must be nonnegative and limit 1..100")
        if status == "all":
            clause, values = "", ()
        elif status == "active":
            clause, values = " AND status IN ('queued','running','waiting_input')", ()
        elif status == 'terminal':
            clause, values = " AND status IN ('done','failed','cancelled')", ()
        elif status in {"queued", "running", "waiting_input", "done", "failed", "cancelled"}:
            clause, values = " AND status=?", (status,)
        else:
            raise ValueError(f"unknown task status filter: {status!r}")
        if since is not None:
            clause += ' AND created>=?'
            values += (since,)
        if before is not None:
            clause += ' AND created<?'
            values += (before,)
        discarded = "EXISTS(SELECT 1 FROM task_events e WHERE e.scene=tasks.scene AND e.task_id=tasks.id AND e.kind='workspace_discard')"
        if environment == 'discarded':
            clause += ' AND ' + discarded
        elif environment == 'retained':
            clause += " AND status IN ('done','failed','cancelled') AND account_browser=0 AND container IS NULL AND browser_active=0 AND NOT " + discarded
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
        continuation = self.continuation(item)
        return item.requester if continuation is None else continuation.requester

    def continuation(self, item: Task) -> TaskInput | None:
        rows = self.db.execute(
            "SELECT body FROM task_events "
            "WHERE scene=? AND task_id=? AND kind='input' ORDER BY id DESC", (item.scene, item.id),
        )
        for row in rows:
            try:
                actual = TaskInput.model_validate_json(row[0])
            except ValidationError as error:
                raise ValueError(f'Invalid task input: {error}; raw={row[0][:1000]!r}') from error
            if actual.mode == 'continue':
                return actual
        return None

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
        return self._event_record(row)

    @staticmethod
    def _event_record(row: sqlite3.Row) -> dict:
        try:
            body = json.loads(row['body'], parse_constant=_reject_constant)
        except ValueError as error:
            raise ValueError(f'Invalid task event {row["id"]}: {error}; raw={row["body"][:500]!r}') from error
        return {**dict(row), 'body': body}

    def event_page(self, scene: str, id: int, *, offset: int, snapshot: int | None) -> dict:
        self.get(scene, id)
        maximum = self.db.execute('SELECT COALESCE(MAX(id),0) FROM task_events WHERE scene=? AND task_id=?',
                                  (scene, id)).fetchone()[0]
        boundary = maximum if snapshot is None else snapshot
        if boundary > maximum:
            raise ValueError(f'Task event snapshot exceeds actual task range: task={id}, snapshot={boundary}, maximum={maximum}')
        rows = self.db.execute('SELECT * FROM task_events WHERE scene=? AND task_id=? AND id<=? '
                               'ORDER BY id DESC LIMIT 6 OFFSET ?', (scene, id, boundary, offset)).fetchall()
        return {'snapshot': boundary, 'offset': offset, 'next_offset': offset + 5 if len(rows) > 5 else None,
                'events': [self._event_record(row) for row in rows[:5]]}

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

    def input_sources(self, scene: str, task_id: int) -> dict[str, dict]:
        rows = self.db.execute("SELECT body FROM task_events WHERE scene=? AND task_id=? AND kind='material_inputs' ORDER BY id",
                               (scene, task_id))
        return {item['name']: item for row in rows for item in json.loads(row['body'])['files']}

    def file_sources(self, scene: str, task_id: int) -> dict[int, dict | None]:
        rows = self.db.execute("SELECT json_extract(body,'$.id'),json_extract(body,'$.source') FROM task_events "
                               "WHERE scene=? AND task_id=? AND kind='file' ORDER BY id", (scene, task_id))
        return {row[0]: None if row[1] is None else json.loads(row[1]) for row in rows}

    def browser_file_sources(self, scene: str, task_id: int) -> dict[str, dict]:
        rows = self.db.execute("SELECT created,body FROM task_events WHERE scene=? AND task_id=? "
                               "AND kind='browser_file' ORDER BY id", (scene, task_id))
        return {body['reference']['path']: {'created': row['created'], **body['source']}
                for row in rows for body in [json.loads(row['body'])]}

    def browser_start(self, scene: str, task_id: int) -> dict | None:
        row = self.db.execute("SELECT created,body FROM task_events WHERE scene=? AND task_id=? "
                              "AND kind='browser_started' ORDER BY id DESC LIMIT 1", (scene, task_id)).fetchone()
        return None if row is None else {'created': row['created'], **json.loads(row['body'])}

    def file_deletions(self, scene: str, task_id: int) -> dict[int, dict]:
        return {row['file_id']: {'created': row['created'], 'requester': row['requester']}
                for row in self.db.execute("SELECT created,json_extract(body,'$.file_id') AS file_id,"
                    "json_extract(body,'$.requester') AS requester FROM task_events "
                    "WHERE scene=? AND task_id=? AND kind='file_deleted' ORDER BY id", (scene, task_id))}

    def latest_cleanup(self, scene: str, task_id: int) -> dict | None:
        row = self.db.execute("SELECT kind,created,body FROM task_events WHERE scene=? AND task_id=? "
                              "AND kind IN ('temporary_cleanup','workspace_discard_result') ORDER BY id DESC LIMIT 1",
                              (scene, task_id)).fetchone()
        return None if row is None else {'kind': row['kind'], 'created': row['created'], **json.loads(row['body'])}

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
