"""Scene-owned work tasks: scheduling, native Pi questions and copied files."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal
from functools import partial
import json
from pathlib import Path
from string import Template
import traceback
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .config import HostConfig
from .egress_usage import EgressUsage
from .model_slots import ModelSlots
from .memory import MemoryService
from .pricing import cost_summary
from .recall import RecallArguments, recall_chat
from .sandbox import DockerSandbox, DockerSettings
from .skills import Skill, load_task_skills, merge_task_skills
from .store import Store
from .task_live import TaskLiveText
from .tasks_store import Task, TaskFile, TaskStore
from .worker_model import Limits
from .worker_session import WorkerSession, worker_session


PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


class DeliverFile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    path: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=240)
    note: str

    @field_validator("name")
    @classmethod
    def filename(cls, value: str) -> str:
        if value in {".", ".."} or any(char in value for char in ("/", "\\", "\x00", "\r", "\n")):
            raise ValueError("name must be a filename, not a path")
        return value


@dataclass
class RunningTask:
    item: Task
    job: asyncio.Task | None = None
    session: WorkerSession | None = None
    answer: asyncio.Future[dict] | None = None
    timer: asyncio.Timeout | None = None
    remaining: float = 0
    cancelled: bool = False
    last_progress: str | None = None
    next_progress: float = 300
    live_text: TaskLiveText = field(default_factory=TaskLiveText)
    skills: tuple[Skill, ...] = ()


def file_info(file: TaskFile, records: TaskStore) -> dict:
    return {"id": file.id, "task_id": file.task_id, "name": file.name,
            "size": file.size, "note": file.note, "status": "registered",
            "upload": records.latest_file_upload(file)}


class WorkTasks:
    def __init__(self, config: HostConfig, store: Store, slots: ModelSlots | None,
                 on_update: Callable[[str], None], *, skills: dict[str, tuple[Skill, ...]],
                 memory: MemoryService | None, data_tools: dict[str, list[dict]],
                 skill_permissions: dict[str, Literal["all"] | list[str]]):
        self.config, self.store, self.slots = config, store, slots
        self.settings = config.worker
        self.records = TaskStore(store)
        self.on_update = on_update
        self.skills = skills
        self.skill_permissions = skill_permissions
        self.memory = memory
        self.data_tools = data_tools
        self.egress = EgressUsage(config, self.records, on_update)
        settings = self.settings
        self.sandbox = DockerSandbox(DockerSettings(
            docker_binary=settings.docker_binary, docker_host=settings.docker_host,
            image=settings.image, workspace_root=settings.workspace_root,
            runtime_root=settings.runtime_root, uid=settings.uid, gid=settings.gid,
            cpus=settings.cpus, memory=settings.memory, pids_limit=settings.pids_limit,
            command_timeout_seconds=settings.command_timeout_seconds,
        ))
        self.running: dict[int, RunningTask] = {}
        self.live_listeners: dict[int, set[asyncio.Event]] = {}
        self.changed = asyncio.Event()
        self.accepting = False
        self._pump: asyncio.Task | None = None
        self.error: str | None = None
        self._failure: asyncio.Future[BaseException] = asyncio.get_running_loop().create_future()

    def _notify(self, scene: str) -> None:
        self.changed.set()
        self.on_update(scene)

    def _roles(self, scene: str, requester: str) -> set[str]:
        if requester == self.config.bot_qq:
            raise PermissionError("委托与管理任务须使用实际人类 QQ，不能用 Bot 账号")
        settings = self.config.scenes[scene].tasks
        roles = {"member"}
        if requester == settings.owner:
            roles.add("owner")
        if requester in settings.admins:
            roles.add("admin")
        if requester in settings.whitelist:
            roles.add("whitelist")
        if scene.startswith("group:") and self.store.latest_sender_role(scene, requester) in {"admin", "owner"}:
            roles.add("group_manager")
        return roles

    def _can_delegate(self, scene: str, requester: str) -> None:
        if not self.accepting:
            raise RuntimeError(f"任务执行器未接受新任务：{self.error or '宿主正在启动或停止'}")
        settings = self.config.scenes[scene].tasks
        if not settings.enabled:
            raise PermissionError("当前场景未开放任务执行")
        if not self._roles(scene, requester).intersection(settings.delegate_roles):
            raise PermissionError(f"QQ {requester} 没有当前场景的委托任务权限")

    def _can_manage(self, item: Task, requester: str) -> None:
        roles = self._roles(item.scene, requester)
        if requester != item.requester and not roles.intersection(
                self.config.scenes[item.scene].tasks.manage_roles):
            raise PermissionError("只能管理自己的任务，或由有任务管理权限的账号操作")

    def status(self, scene: str, id: int) -> dict:
        item = self.records.get(scene, id)
        costs = self.records.call_costs(scene, id)
        return {**asdict(item), "files": [file_info(file, self.records) for file in self.records.list_files(scene, id)],
                "model_calls": len(costs), "cost": cost_summary(costs),
                "network": self.egress.status(scene, id),
                "notice": "done 只表示执行正常结束；文件登记不表示已上传 QQ。出网配置不等于目标连通。"}

    def live_snapshot(self, scene: str, id: int) -> dict:
        item = self.records.get(scene, id)
        current = self.running.get(id)
        return {"task_id": item.id, "scene": item.scene, "status": item.status,
                "preview": None if current is None else current.live_text.snapshot()}

    def _notify_live(self, id: int) -> None:
        for listener in self.live_listeners.get(id, ()):
            listener.set()

    def list(self, scene: str, *, status: str = "active", offset: int = 0, limit: int = 20) -> dict:
        items = self.records.list(scene, status=status, offset=offset, limit=limit + 1)
        return {"items": [asdict(item) for item in items[:limit]],
                "next_offset": offset + limit if len(items) > limit else None}

    async def delegate(self, scene: str, *, requester: str, goal: str,
                       deliverable: str, context: str) -> dict:
        self._can_delegate(scene, requester)
        local = datetime.fromtimestamp(self.store.now(), ZoneInfo(self.config.timezone))
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        if self.records.count_created(scene, requester, midnight) >= self.config.scenes[scene].tasks.max_daily_tasks:
            raise PermissionError(f"QQ {requester} 今天在本场景的任务次数已达上限")
        prompt = Template((PROMPTS / "next_worker.md").read_text()).substitute(
            scene=scene, requester=requester, goal=goal, deliverable=deliverable, context=context)
        item = self.records.create(scene, requester, goal, deliverable, context, prompt)
        self._notify(scene)
        return self.status(scene, item.id)

    async def append(self, scene: str, id: int, *, requester: str, text: str) -> dict:
        if not self.accepting:
            raise RuntimeError("任务执行器正在启动或停止，不能追加运行要求")
        item = self.records.get(scene, id)
        self._can_manage(item, requester)
        if item.status not in {"running", "waiting_input"}:
            raise ValueError("追加只适用于已开始的任务；已结束任务用 continue")
        current = self.running[id]
        if current.session is None:
            raise ValueError("任务容器仍在启动，请稍后追加")
        await current.session.pi.command("steer", message=text)
        self.records.add_event(scene, id, "input", {"requester": requester, "text": text, "mode": "steer"})
        self._notify(scene)
        return {"id": id, "status": "steering_queued", "text": text}

    async def resume(self, scene: str, id: int, *, requester: str, text: str) -> dict:
        item = self.records.get(scene, id)
        self._can_manage(item, requester)
        self._can_delegate(scene, requester)
        self._limits(item)
        self.records.requeue(scene, id, text, requester=requester)
        self._notify(scene)
        return self.status(scene, id)

    async def answer(self, scene: str, id: int, *, requester: str,
                     text: str | None, confirmed: bool | None, question_id: str) -> dict:
        if not self.accepting:
            raise RuntimeError("任务执行器正在启动或停止，不能恢复等待中的任务")
        item = self.records.get(scene, id)
        if requester == self.config.bot_qq:
            raise PermissionError("回答者必须是实际人类 QQ，不用 Bot 冒充回答者")
        if item.status != "waiting_input":
            raise ValueError("任务当前没有等待回答的问题")
        if item.question["id"] != question_id:
            raise ValueError("当前问题已经变化；请重读任务后针对新的问题回答，没有发送这次旧答复")
        current = self.running[id]
        if current.answer.done():
            raise ValueError("当前问题已收到回答或已超时，正在等待执行槽")
        if item.question["method"] == "confirm":
            self._can_manage(item, requester)
            if confirmed is None or text is not None:
                raise ValueError("操作确认必须使用 confirmed 布尔值，普通文字不能替代授权")
            response = {"confirmed": confirmed}
        else:
            if text is None or confirmed is not None:
                raise ValueError("补充信息必须用 text，不是操作授权")
            if item.question["method"] == "select" and text not in item.question["options"]:
                raise ValueError("回答必须是当前问题提供的一个选项")
            response = {"value": text}
        self.records.add_event(scene, id, "answer", {"requester": requester, **response})
        current.answer.set_result(response)
        self._notify(scene)
        return {"id": id, "status": "answer_received", "notice": "等待执行槽后继续原任务"}

    async def cancel(self, scene: str, id: int, *, requester: str) -> dict:
        item = self.records.get(scene, id)
        self._can_manage(item, requester)
        if item.status not in {"queued", "running", "waiting_input"}:
            raise ValueError(f"任务已是 {item.status}")
        if item.status != "queued" and not self.accepting:
            raise RuntimeError("宿主正在处理活动任务的启动或中断，请待处理结束后查看结果")
        self.records.add_event(scene, id, "cancel", {"requester": requester})
        if item.status == "queued":
            self._finish(item, "cancelled", None, f"QQ {requester} 取消排队任务")
        elif item.status in {"running", "waiting_input"}:
            current = self.running[id]
            current.cancelled = True
            if not current.job.cancelling():
                current.job.cancel()
            await asyncio.gather(current.job, return_exceptions=True)
        return self.status(scene, id)

    def _limits(self, item: Task) -> Limits:
        costs = self.records.call_costs(item.scene, item.id)
        calls = self.settings.max_calls - len(costs)
        if calls <= 0:
            raise ValueError("任务累计模型调用次数已达上限")
        budget = self.settings.max_cost
        if budget is not None:
            binding = self.config.models.roles.worker
            price = self.config.models.prices[binding.provider][binding.model]
            if any(cost is None or cost["currency"] != price.currency for cost in costs):
                raise ValueError("任务存在未知或不同币种费用，不能继续金额受限的请求")
            budget -= sum((Decimal(cost["amount"]) for cost in costs), Decimal(0))
            if budget <= 0:
                raise ValueError("任务累计模型费用已达上限")
        return Limits(calls, self.settings.max_request_bytes, self.settings.max_response_bytes, budget)

    async def recover(self) -> None:
        """Clean interrupted work even when the platform cannot connect."""
        for item in self.records.containers():
            await self.sandbox.stop_recorded(item.scene, str(item.id), item.container)
            self.records.set_container(item.scene, item.id, None)
        for item in self.records.active():
            self._finish(item, "failed", item.summary, "宿主中断；保留会话与未答问题，须显式继续")
        self.egress.recover()

    async def start(self) -> None:
        self.accepting = True
        self._pump = asyncio.create_task(self._schedule())

    def stop(self) -> None:
        self.accepting = False
        self.changed.set()

    async def close(self) -> None:
        self.stop()
        try:
            if self._pump is not None:
                await self._pump
        finally:
            jobs = [current.job for current in self.running.values()]
            for job in jobs:
                if not job.cancelling():
                    job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)

    async def wait_failure(self) -> None:
        raise await asyncio.shield(self._failure)

    def _fail(self, error: BaseException) -> None:
        self.error = "".join(traceback.format_exception_only(error)).strip()
        self.stop()
        if not self._failure.done():
            self._failure.set_result(error)
        for scene in self.config.scenes:
            self.on_update(scene)

    def _job_done(self, current: RunningTask, job: asyncio.Task) -> None:
        try:
            if job.cancelled():
                # Cancellation before the coroutine's first step has no finally.
                if current.item.id in self.running:
                    self._finish(current.item, "cancelled" if current.cancelled else "failed",
                                 None, "任务在开始执行前被中断")
                    self.running.pop(current.item.id)
                    self._notify_live(current.item.id)
            elif (error := job.exception()) is not None:
                self._fail(error)
        except Exception as error:
            self._fail(error)

    def _room(self, scene: str, *, new_container: bool) -> bool:
        active = [self.records.get(run.item.scene, run.item.id) for run in self.running.values()]
        executing = [item for item in active if item.status == "running"]
        if (len(executing) >= self.settings.max_running
                or sum(item.scene == scene for item in executing) >= self.config.scenes[scene].tasks.max_running):
            return False
        containers = [*active, *(item for item in self.records.containers() if item.id not in self.running)]
        return not new_container or (len(containers) < self.settings.max_containers
            and sum(item.scene == scene for item in containers) < self.settings.max_scene_containers)

    async def _schedule(self) -> None:
        try:
            while self.accepting:
                self.changed.clear()
                resumable = any(
                    run.answer is not None and run.answer.done() and not run.cancelled
                    and self.records.get(run.item.scene, run.item.id).status == "waiting_input"
                    and self._room(run.item.scene, new_container=False)
                    for run in self.running.values())
                if resumable:
                    # Woken question consumers claim capacity before new work.
                    await self.changed.wait()
                    continue
                for item in self.records.queued():
                    if item.scene not in self.config.scenes:
                        self._finish(item, "failed", None, "任务原场景不在当前根配置中")
                        continue
                    try:
                        self._can_delegate(item.scene, self.records.execution_requester(item))
                        self._limits(item)
                    except (ValueError, PermissionError) as error:
                        self._finish(item, "failed", None, str(error))
                        continue
                    if self._room(item.scene, new_container=True):
                        current = RunningTask(self.records.start(item.scene, item.id))
                        self.running[item.id] = current
                        self._notify_live(item.id)
                        current.job = asyncio.create_task(self._run(current))
                        current.job.add_done_callback(lambda job, current=current: self._job_done(current, job))
                        self.on_update(item.scene)
                await self.changed.wait()
        except Exception as error:
            self._fail(error)

    def _finish(self, item: Task, status: str, summary: str | None, error: str | None) -> None:
        finished = self.records.finish(item.scene, item.id, status, summary, error)
        files = [file_info(file, self.records) for file in self.records.list_files(item.scene, item.id)]
        body = {"status": status, "summary": summary, "error": error, "files": files,
                "started": finished.started, "ended": finished.ended,
                "cost": cost_summary(self.records.call_costs(item.scene, item.id))}
        notice = f"[任务执行结束] #{item.id}；请求人 QQ {item.requester}；{item.goal}\n" + json.dumps(body, ensure_ascii=False)
        self.records.add_event(item.scene, item.id, "finished", body, notice=notice)
        self._notify(item.scene)

    async def _run(self, current: RunningTask) -> None:
        item = current.item
        binding = self.config.models.roles.worker
        price = self.config.models.prices.get(binding.provider, {}).get(binding.model)
        status, summary, error_text = "failed", None, None
        try:
            async with asyncio.timeout(self.settings.active_timeout_seconds) as timer:
                current.timer = timer
                current.skills = self.skills[item.scene]
                if (self.settings.skills_directory is not None
                        and self.skill_permissions[item.scene] == "all"):
                    workspace = self.settings.workspace_root / item.scene / "tasks" / str(item.id)
                    authored = await asyncio.to_thread(load_task_skills, workspace)
                    current.skills = merge_task_skills(current.skills, authored)
                async with worker_session(
                    self.sandbox, scene=item.scene, task_id=str(item.id),
                    skills=current.skills,
                    data_tools=self.data_tools[item.scene],
                    task_timeout_seconds=self.settings.active_timeout_seconds,
                    settings=self.config.model_settings("worker"), provider=binding.provider,
                    context_window_tokens=binding.context_window_tokens, price=price,
                    limits=self._limits(item), model_reasoning=self.settings.model_reasoning,
                    input_support=self.settings.input_support, slots=self.slots,
                    compaction_reserve_tokens=self.settings.compaction_reserve_tokens,
                    compaction_keep_recent_tokens=self.settings.compaction_keep_recent_tokens,
                    egress_settings=self.settings.egress,
                    egress_bytes_per_second=(
                        self.settings.egress.bytes_per_second
                        if self.config.scenes[item.scene].tasks.egress_bytes_per_second is None
                        else self.config.scenes[item.scene].tasks.egress_bytes_per_second
                    ),
                    before_bytes=partial(self.egress.before_bytes, item.scene, item.id),
                    on_bytes=partial(self.egress.on_bytes, item.scene, item.id),
                    on_connection=partial(self.egress.on_connection, item.scene, item.id),
                    start_call=lambda facts: self.records.start_call(item.scene, item.id, facts),
                    finish_call=self.records.finish_call,
                    on_container=lambda container: self.records.set_container(item.scene, item.id, container),
                    task_request=lambda path, raw: self._request(current, path, raw),
                ) as session:
                    current.session = session
                    consuming = asyncio.create_task(self._consume(current))
                    failed = asyncio.create_task(session.wait_failure())
                    try:
                        done, _ = await asyncio.wait({consuming, failed}, return_when=asyncio.FIRST_COMPLETED)
                        if failed in done:
                            await failed
                        summary = await consuming
                    finally:
                        consuming.cancel()
                        failed.cancel()
                        await asyncio.gather(consuming, failed, return_exceptions=True)
                self.records.set_container(item.scene, item.id, None)
                status = "done"
        except asyncio.CancelledError:
            status = "cancelled" if current.cancelled else "failed"
            error_text = "任务被明确取消" if current.cancelled else "宿主停止，中断活动任务"
        except Exception as error:
            error_text = "".join(traceback.format_exception_only(error)).strip()
        finally:
            closing = asyncio.create_task(self._finalize(current, status, summary, error_text))
            while not closing.done():
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            closing.result()

    async def _finalize(self, current: RunningTask, status: str, summary: str | None,
                        error_text: str | None) -> None:
        item = current.item
        try:
            actual = self.records.get(item.scene, item.id)
            if actual.container is not None:
                try:
                    await self.sandbox.stop_recorded(item.scene, str(item.id), actual.container)
                    self.records.set_container(item.scene, item.id, None)
                except Exception as error:
                    status = "failed"
                    error_text = f"{error_text or ''}\n容器清理失败：{type(error).__name__}: {error}"
            self._finish(item, status, summary, error_text)
        finally:
            try:
                self.egress.release_task(item.scene, item.id)
            finally:
                self.running.pop(item.id)
                self._notify_live(item.id)

    async def _consume(self, current: RunningTask) -> str:
        item, session = current.item, current.session
        # PiRpc's reader already drains stdout while prompt is being accepted.
        environment = Template((PROMPTS / "next_worker_environment.md").read_text()).substitute(
            facts=json.dumps({
                "public_network": session.egress is not None,
                "proxy": None if session.egress is None else f"http://127.0.0.1:{session.egress.port}",
                "task_traffic": self.egress.status(item.scene, item.id),
                "scene_today_traffic": self.egress.status(item.scene),
                "data_tools": [tool["name"] for tool in self.data_tools[item.scene]],
                "skills": [{"name": skill.name, "source": skill.source,
                            "path": skill.container_path + "/SKILL.md"}
                           for skill in current.skills if not skill.disable_model_invocation],
            }, ensure_ascii=False, allow_nan=False),
        )
        if await session.pi.prompt(item.input + "\n\n" + environment) == "handled":
            raise RuntimeError("Pi 处理了输入但未开始执行任务")
        final: dict | None = None
        while True:
            elapsed = self.settings.active_timeout_seconds - max(
                0, current.timer.when() - asyncio.get_running_loop().time())
            if elapsed >= current.next_progress:
                self._progress(current, current.last_progress or "任务仍在执行，尚未报告阶段说明")
                current.next_progress = elapsed + 600
            try:
                record = await asyncio.wait_for(session.pi.next_event(), timeout=60)
            except TimeoutError:
                continue
            body = record.body
            if record.type == "message_update":
                if current.live_text.update(body["assistantMessageEvent"]):
                    self._notify_live(item.id)
                continue
            if record.type in {"tool_execution_update", "bash_execution_update"}:
                continue
            self.records.add_event(item.scene, item.id, "native", body)
            self.on_update(item.scene)
            if record.type == "message_start":
                if current.live_text.start(body["message"]):
                    self._notify_live(item.id)
            elif record.type == "message_end" and body["message"]["role"] == "assistant":
                if current.live_text.finish(body["message"]):
                    self._notify_live(item.id)
                final = body["message"]
            elif record.type == "entry_appended":
                entry = body["entry"]
                if entry.get("customType") == "lenbot_progress":
                    self._progress(current, entry["data"]["text"])
            elif record.type == "extension_ui_request" and body["method"] in {"input", "confirm", "select", "editor"}:
                await self._question(current, body)
            elif record.type in {"auto_retry_start", "summarization_retry_scheduled"}:
                raise RuntimeError(f"Pi 发起了未启用的重试：{body!r}")
            elif record.type == "agent_settled":
                if final is None:
                    raise RuntimeError("Pi 停止但没有最终助手消息")
                if final["stopReason"] != "stop":
                    raise RuntimeError(f"Pi 未正常完成：{json.dumps(final, ensure_ascii=False)}")
                return "\n".join(part["text"] for part in final["content"] if part["type"] == "text")

    def _progress(self, current: RunningTask, text: str) -> None:
        current.last_progress = text
        item = current.item
        self.records.add_event(item.scene, item.id, "progress", {"text": text},
                               notice=f"[任务进度] #{item.id}；{item.goal}\n{text}")
        self._notify(item.scene)

    async def _question(self, current: RunningTask, body: dict) -> None:
        item = current.item
        if (not isinstance(body.get("title"), str)
                or body["method"] == "confirm" and not isinstance(body.get("message"), str)):
            raise ValueError(f"Pi 问题缺少原文：{body!r}")
        if body["method"] == "select" and (not isinstance(body.get("options"), list)
                or not all(isinstance(option, str) for option in body["options"])):
            raise ValueError(f"Pi 选择题选项无效：{body!r}")
        loop = asyncio.get_running_loop()
        current.remaining = current.timer.when() - loop.time()
        if current.remaining <= 0:
            raise TimeoutError("任务活动执行时长已达上限")
        current.timer.reschedule(None)
        current.answer = loop.create_future()
        self.records.set_question(item.scene, item.id, body)
        self.records.add_event(item.scene, item.id, "question", body,
            notice=f"[任务{'操作确认' if body['method'] == 'confirm' else '提问'}] #{item.id}；"
                   f"请求人 QQ {item.requester}\n{json.dumps(body, ensure_ascii=False)}")
        self._notify(item.scene)
        try:
            response = await asyncio.wait_for(current.answer, timeout=self.settings.input_timeout_seconds)
        except TimeoutError:
            response = {"confirmed": False} if body["method"] == "confirm" else {"cancelled": True}
            self.records.add_event(item.scene, item.id, "answer_timeout", response)
        while not self._room(item.scene, new_container=False):
            self.changed.clear()
            await self.changed.wait()
        self.records.set_question(item.scene, item.id, None)
        current.timer.reschedule(loop.time() + current.remaining)
        await current.session.pi.respond_ui(body["id"], **response)
        self._notify(item.scene)

    async def _request(self, current: RunningTask, path: str, raw: bytes) -> dict:
        if path in {"/task/recall-chat", "/task/memory"}:
            name = "recall_chat" if path == "/task/recall-chat" else "memory"
            scene = current.item.scene
            if name not in {tool["name"] for tool in self.data_tools[scene]}:
                raise PermissionError(f"当前场景的任务未开放 {name}")
            if name == "recall_chat":
                try:
                    arguments = RecallArguments.model_validate_json(raw)
                except ValidationError as error:
                    raise ValueError(f"Invalid task recall_chat request: {raw[:500]!r}; {error}") from error
                content = recall_chat(self.store, scene, self.config.timezone, arguments)
            else:
                try:
                    arguments = json.loads(raw)
                except (json.JSONDecodeError, UnicodeDecodeError) as error:
                    raise ValueError(f"Invalid task memory JSON: {raw[:500]!r}; {error}") from error
                content = await self.memory.execute(scene, arguments)
            return {"content": content}
        if path == "/task/network":
            try:
                request = json.loads(raw)
            except ValueError as error:
                raise ValueError(f"Invalid task network request: {raw[:500]!r}; {error}") from error
            if request != {}:
                raise ValueError(f"Task network status expects an empty object: {raw[:500]!r}")
            return {"task": self.egress.status(current.item.scene, current.item.id),
                    "scene_today": self.egress.status(current.item.scene)}
        arguments = DeliverFile.model_validate_json(raw)
        item = current.item
        destination = self.settings.delivery_root / item.scene / str(item.id)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / uuid4().hex
        try:
            await self.sandbox.copy_out(current.session.sandbox, arguments.path, target,
                                        max_bytes=self.settings.max_file_bytes)
            file = self.records.add_file(item.scene, item.id, name=arguments.name, path=str(target),
                                         size=target.stat().st_size, note=arguments.note)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        self.records.add_event(item.scene, item.id, "file", file_info(file, self.records))
        self._notify(item.scene)
        return file_info(file, self.records)
