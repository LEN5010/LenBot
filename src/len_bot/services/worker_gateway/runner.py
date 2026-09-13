"""The Gateway's execution runner: start, watch, stop, record.

Everything about *how* a container is built lives here, and only here.  The
command is assembled from the deployment's own image registry and the stored
request; nothing in the request can add a mount, change the user, or name a
host path.

Two timing rules are the reason this is a separate service rather than a call
into the Bot process:

* the deadline is absolute and stored at acceptance time, so a stop happens on
  the Gateway's own clock even when LenBot is not running;
* a stop is carried out and then *confirmed*.  ``cancel_requested`` is what was
  asked for; ``termination_confirmed``/``termination_unconfirmed`` is what
  actually happened, and the second one is a real answer that keeps the
  workspace blocked rather than a soft success.
"""
from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import signal
import stat
import time
import uuid
from pathlib import Path

from len_bot.execution.protocol import (
    ExecutionRecord, ExecutionRequest, ExecutionState, TerminationReport,
)
from len_bot.services.worker_gateway.config import GatewayConfig, WorkerImage
from len_bot.services.worker_gateway.store import GatewayStore

_CONTAINER_PREFIX = 'lenbot-x-'
_SAFE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


class GatewayRefusal(ValueError):
    """A request the Gateway refuses before anything is started."""


class ExecutionRunner:
    def __init__(self, config: GatewayConfig, store: GatewayStore,
                 workspaces_root: Path, controls_root: Path, clock=time.time,
                 runtime_path: str | None = None):
        self.config = config
        self.store = store
        self.workspaces = workspaces_root
        self.controls = controls_root
        self.clock = clock
        self.runtime_path = runtime_path or config.runtime_path
        self._tasks: dict[str, asyncio.Task] = {}
        self._accept_lock = asyncio.Lock()

    # ---- workspace and control directories ---------------------------------
    def workspace_directory(self, workspace_id: str) -> Path:
        self._require_safe_name(workspace_id, 'workspace_id')
        self.workspaces.mkdir(parents=True, exist_ok=True, mode=0o770)
        root = self.workspaces.resolve()
        path = (self.workspaces / workspace_id).resolve()
        if root not in path.parents:
            raise GatewayRefusal('工作目录越出网关配置的根目录')
        path.mkdir(parents=True, exist_ok=True, mode=0o770)
        return path

    def control_directory(self, execution_id: str) -> Path:
        self._require_safe_name(execution_id, 'execution_id')
        self.controls.mkdir(parents=True, exist_ok=True, mode=0o700)
        root = self.controls.resolve()
        path = (self.controls / execution_id).resolve()
        if root not in path.parents:
            raise GatewayRefusal('控制目录越出网关配置的根目录')
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path

    @staticmethod
    def _require_safe_name(value: str, field: str) -> None:
        if not _SAFE_NAME.fullmatch(value or ''):
            raise GatewayRefusal(f'{field} 不是合法的执行标识')

    def container_name(self, execution_id: str) -> str:
        """The container identity a recorded run can be stopped by.

        Derived from the execution id alone, so a stop issued by a different
        process — an operator call, or a Gateway that restarted — targets the
        same container the run created rather than a random name it cannot
        find.
        """
        self._require_safe_name(execution_id, 'execution_id')
        return _CONTAINER_PREFIX + execution_id

    # ---- acceptance --------------------------------------------------------
    async def accept(self, request: ExecutionRequest) -> tuple[ExecutionRecord, bool]:
        """Admit one execution, or return the one already stored under this id.

        Capacity is checked only for a genuinely new execution: a client that
        retries the same id after a timeout must get its own stored record back
        even when the Gateway is currently full, otherwise the retry would be
        refused exactly when the caller most needs to find out what happened.
        """
        async with self._accept_lock:
            if await self.store.get_execution(request.execution_id) is not None:
                return await self.store.record_execution(request)
            if await self.store.count_unfinished() >= self.config.max_concurrent:
                raise GatewayRefusal(
                    f'网关已有 {self.config.max_concurrent} 个未终结执行；本次不排队、不启动，'
                    '请稍后以同一执行 ID 重试')
            try:
                self.config.worker_for(request.image_ref, request.worker_type)
                self.config.policy_for(request.network_policy)
            except KeyError as error:
                raise GatewayRefusal(str(error.args[0])) from None
            record, created = await self.store.record_execution(request)
            if not created:
                return record, False
            if request.input_assets:
                # Input bytes are transferred by the host export path, which
                # this version does not implement.  Starting a run that never
                # receives them would let the script read a missing file and
                # look like a task failure instead of an absent feature.
                await self.store.append_execution_event(
                    request.execution_id, 'inputs_unsupported',
                    f'本版不接受输入资料导入（{len(request.input_assets)} 项）；执行未启动',
                    state=ExecutionState.FAILED,
                    error='输入资料导入尚未实现，执行未启动')
                return await self._stored(request.execution_id), True
            await self.store.append_execution_event(request.execution_id, 'accepted',
                                                    '执行已登记；容器尚未启动')
            self._tasks[request.execution_id] = asyncio.create_task(self._run(request))
            return await self._stored(request.execution_id), True

    async def _stored(self, execution_id: str) -> ExecutionRecord:
        record = await self.store.get_execution(execution_id)
        if record is None:
            raise GatewayRefusal(f'执行 {execution_id} 不在执行日志中')
        return record

    # ---- the run -----------------------------------------------------------
    async def _run(self, request: ExecutionRequest) -> None:
        execution_id = request.execution_id
        container_name = self.container_name(execution_id)
        process = None
        streams: list[asyncio.Task] = []
        try:
            record = await self._stored(execution_id)
            worker = self.config.worker_for(request.image_ref, request.worker_type)
            policy = self.config.policy_for(request.network_policy)
            control = self.control_directory(execution_id)
            workspace = self.workspace_directory(request.workspace_id)
            self._write_control(control / 'task.py', request.script)
            command = self._command(container_name, worker, policy.mode, control, workspace)
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True)
            except OSError as error:
                await self._fail(execution_id, 'start_failed', f'容器运行时不可用：{error}')
                return
            await self.store.append_execution_event(
                execution_id, 'starting', f'容器 {container_name} 已创建，等待运行确认',
                state=ExecutionState.STARTING)
            await self._confirm_started(execution_id, container_name)
            streams = [asyncio.create_task(self._read_limited(process.stdout)),
                       asyncio.create_task(self._read_limited(process.stderr))]
            stopped = await self._watch(execution_id, container_name, process, record.deadline_at)
            if not stopped:
                await process.wait()
            else:
                await self._collect(process)
            for stream in streams:
                if not stream.done():
                    stream.cancel()
            results = await asyncio.gather(*streams, return_exceptions=True)
            stdout, stdout_truncated = self._stream_result(results, 0)
            stderr, stderr_truncated = self._stream_result(results, 1)
            current = await self.store.get_execution(execution_id)
            if stopped or current is None or current.state is not ExecutionState.RUNNING:
                # The stop path already recorded the outcome; a run that was
                # stopped is not also an exit.
                return
            state = ExecutionState.EXITED if process.returncode == 0 else ExecutionState.FAILED
            await self.store.append_execution_event(
                execution_id, 'exited' if state is ExecutionState.EXITED else 'failed',
                f'进程以返回码 {process.returncode} 结束', state=state,
                returncode=process.returncode,
                error=None if state is ExecutionState.EXITED else '进程以非零返回码结束',
                stdout=stdout, stderr=stderr, stdout_truncated=stdout_truncated,
                stderr_truncated=stderr_truncated)
            await self._register_artifacts(execution_id, workspace)
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(execution_id, container_name, process,
                                            reason='网关正在停止该执行'))
            raise
        except Exception as error:  # recorded as the run's own failure, never swallowed silently
            await self._fail(execution_id, 'runner_error', f'执行器失败：{error}')
        finally:
            for stream in streams:
                if not stream.done():
                    stream.cancel()
            self._tasks.pop(execution_id, None)

    async def shutdown(self) -> None:
        """Stop every run this process is watching and confirm each stop.

        Called on the way out, so shutting the Gateway down is not a way to
        leave a container running that nothing can name afterwards.
        """
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # A run with no live task (accepted but never started, or an older
        # record this process re-read) still owes an outcome; stop it by name.
        for execution_id in await self.store.unfinished():
            await self._stop(execution_id, self.container_name(execution_id), None,
                             reason='网关正在停止，按本地期限停止该执行')

    async def _fail(self, execution_id: str, kind: str, detail: str) -> None:
        await self.store.append_execution_event(execution_id, kind, detail,
                                                state=ExecutionState.FAILED, error=detail)

    @staticmethod
    def _stream_result(results: list, index: int) -> tuple[str, bool]:
        value = results[index]
        return value if isinstance(value, tuple) else ('', False)

    def _command(self, container_name: str, worker: WorkerImage, mode: str,
                 control: Path, workspace: Path) -> list[str]:
        command = [self.runtime_path, 'run', '--rm', '--name', container_name,
                   '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--user', worker.container_user, '--pids-limit', str(worker.pids_limit),
                   '--memory', worker.memory, '--cpus', worker.cpus,
                   '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m']
        # The egress rule comes from the deployment's policy entry, never from
        # the caller: a request names a policy, and an unbuilt policy is
        # refused before this point.
        command += ['--network', mode]
        command += ['-v', f'{workspace}:/workspace:rw', '-v', f'{control}:/lenbot-control:ro',
                    '-w', '/workspace', worker.image, 'python', '/lenbot-control/task.py']
        return command

    async def _confirm_started(self, execution_id: str, container_name: str) -> None:
        """Wait until the runtime reports the container actually running.

        A created client process is not yet a running container, and recording
        ``running`` from the client alone would claim execution that may never
        have begun.
        """
        for _ in range(self.config.start_confirm_attempts):
            running = await self._inspect_running(container_name)
            if running is True:
                await self.store.append_execution_event(execution_id, 'running',
                                                        '容器已确认运行', state=ExecutionState.RUNNING)
                return
            if running is False:
                return
            await asyncio.sleep(self.config.start_confirm_interval_seconds)

    # ---- watching and stopping --------------------------------------------
    async def _watch(self, execution_id: str, container_name: str, process,
                     deadline_at: float) -> bool:
        """Wait for the process or stop it, on the Gateway's own clock.

        Returns true when a stop was carried out here.  The deadline is the
        stored one, so a Gateway that restarted continues against the same
        instant instead of granting a fresh budget, and a cancellation asked
        for by another process is picked up on the next pass rather than
        racing it.
        """
        while process.returncode is None:
            current = await self.store.get_execution(execution_id)
            if current is not None and current.state is ExecutionState.CANCEL_REQUESTED:
                await self._stop(execution_id, container_name, process, reason='收到外部取消请求')
                return True
            remaining = deadline_at - self.clock()
            if remaining <= 0:
                await self._stop(execution_id, container_name, process,
                                 reason='到达执行期限，网关按原期限停止')
                return True
            try:
                await asyncio.wait_for(process.wait(), min(0.5, max(0.05, remaining)))
            except asyncio.TimeoutError:
                continue
        return False

    async def _stop(self, execution_id: str, container_name: str, process,
                    *, reason: str) -> TerminationReport | None:
        """Record the cancellation request, carry it out, then confirm the result."""
        current = await self.store.get_execution(execution_id)
        if current is None or current.state in {ExecutionState.TERMINATION_CONFIRMED,
                                               ExecutionState.TERMINATION_UNCONFIRMED}:
            return current.termination if current is not None else None
        if current.state not in {ExecutionState.EXITED, ExecutionState.FAILED}:
            if current.state is not ExecutionState.CANCEL_REQUESTED:
                await self.store.append_execution_event(execution_id, 'cancel_requested', reason,
                                                        state=ExecutionState.CANCEL_REQUESTED)
        report = await self._terminate(container_name, process)
        state = (ExecutionState.TERMINATION_CONFIRMED if report.status != 'unconfirmed'
                 else ExecutionState.TERMINATION_UNCONFIRMED)
        await self.store.append_execution_event(
            execution_id, 'termination', f'终止结果：{report.status}；{report.detail}', state=state,
            termination=report, error=None if report.status != 'unconfirmed' else '终止未确认')
        if report.status != 'unconfirmed':
            await self._register_artifacts(execution_id,
                                           self.workspace_directory(current.workspace_id))
        return report

    async def cancel(self, execution_id: str, reason: str) -> tuple[ExecutionRecord, bool]:
        """Ask for a stop and report how far it got.

        Returns the stored record and whether a stop was attempted.  When a
        watcher task is running in this process the request is recorded and the
        watcher carries it out; when none is (an operator call, or after a
        restart) this call carries it out itself.  Either way ``cancel_requested``
        alone is not a stop: only the termination state says what happened.
        """
        current = await self._stored(execution_id)
        if current.state in {ExecutionState.EXITED, ExecutionState.FAILED,
                             ExecutionState.TERMINATION_CONFIRMED,
                             ExecutionState.TERMINATION_UNCONFIRMED}:
            return current, False
        task = self._tasks.get(execution_id)
        if task is None:
            await self._stop(execution_id, self.container_name(execution_id), None, reason=reason)
            return await self._stored(execution_id), True
        if current.state is not ExecutionState.CANCEL_REQUESTED:
            await self.store.append_execution_event(execution_id, 'cancel_requested', reason,
                                                    state=ExecutionState.CANCEL_REQUESTED)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self.config.cleanup_timeout_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
        return await self._stored(execution_id), True

    # ---- reconciliation after a restart ------------------------------------
    async def sweep(self) -> None:
        """Settle stored unfinished executions on this Gateway's own clock.

        A restarted Gateway replays nothing: it re-reads what it stored and
        stops whatever is past its deadline.  A container from a previous life
        is never adopted silently — a run whose start this process cannot
        confirm is recorded as unconfirmed, which keeps its workspace blocked
        until an operator looks at it.
        """
        for execution_id in await self.store.unfinished():
            record = await self.store.get_execution(execution_id)
            if record is None or execution_id in self._tasks:
                continue
            if self.clock() >= record.deadline_at:
                await self._stop(execution_id, self.container_name(execution_id), None,
                                 reason='网关重启后发现该执行已超过原期限，按原期限停止')
                continue
            await self.store.append_execution_event(
                execution_id, 'orphaned',
                '网关重启后该执行没有可接续的监视进程；容器状态未确认，需由运营者核对',
                state=ExecutionState.TERMINATION_UNCONFIRMED,
                termination=TerminationReport(
                    status='unconfirmed',
                    detail='网关重启，无法确认上一次运行的容器是否仍存在；目录保持封锁'))

    # ---- container client helpers -----------------------------------------
    async def _inspect_running(self, container_name: str) -> bool | None:
        code, output = await self._run_bounded(self.runtime_path, 'inspect', '--format',
                                               '{{.State.Running}}', container_name)
        if code != 0:
            return None if code is None else False
        return output.strip().lower() == 'true'

    async def _terminate(self, container_name: str, process) -> TerminationReport:
        details: list[str] = []
        deadline = self.clock() + self.config.cleanup_timeout_seconds
        code, output = await self._run_bounded(self.runtime_path, 'kill', container_name)
        if code is None:
            details.append(output or 'container kill did not confirm')
        elif code != 0:
            details.append(output or 'container kill failed')
        if process is not None:
            self._reap(process)
            await self._collect(process)
        status = 'unconfirmed'
        if self.clock() < deadline:
            code, output = await self._run_bounded(self.runtime_path, 'rm', '-f', container_name)
            if code is None:
                details.append(output or 'container removal did not confirm')
            elif code != 0:
                details.append(output or 'container removal failed')
        else:
            details.append('cleanup budget exhausted before container removal')
        # Inspect after rm: --rm and a successful explicit removal both make an
        # absent container a confirmed terminal state.  Never infer safety from
        # removal alone when the final inspection cannot establish it.
        if self.clock() < deadline:
            code, output = await self._run_bounded(
                self.runtime_path, 'inspect', '--format', '{{.State.Running}}', container_name)
            if code is None:
                details.append(output or 'container state could not be inspected')
            elif code != 0:
                lowered = output.lower()
                if 'no such object' in lowered or 'not found' in lowered:
                    status = 'confirmed_absent'
                else:
                    details.append(output)
            elif output.strip().lower() == 'false':
                status = 'confirmed_stopped'
        else:
            details.append('cleanup budget exhausted before container inspection')
        return TerminationReport(status=status, container_name=container_name,
                                 detail='; '.join(item for item in details if item)[:2000])

    async def _run_bounded(self, *argv: str) -> tuple[int | None, str]:
        """Run one bounded runtime command and always reap its own client."""
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
        except OSError as error:
            return None, str(error)
        try:
            await asyncio.wait_for(process.wait(), timeout=self.config.cleanup_timeout_seconds)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self._reap(process)
            await self._collect(process)
            return None, 'runtime client did not exit'
        parts = []
        for stream in (process.stdout, process.stderr):
            if stream is None:
                continue
            try:
                parts.append((await stream.read()).decode('utf-8', 'replace'))
            except OSError:
                pass
        return process.returncode, ('\n'.join(part for part in parts if part))[-1000:]

    @staticmethod
    def _reap(process) -> None:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

    async def _collect(self, process) -> None:
        """Reap a client process; its own output tasks read the pipes to EOF."""
        try:
            await asyncio.shield(asyncio.wait_for(
                process.wait(), timeout=self.config.cleanup_timeout_seconds))
        except (asyncio.TimeoutError, asyncio.CancelledError, OSError):
            pass

    async def _read_limited(self, stream) -> tuple[str, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        limit = self.config.max_output_chars
        while True:
            data = await stream.read(4096)
            if not data:
                break
            if total < limit:
                keep = min(len(data), limit - total)
                chunks.append(data[:keep])
                total += keep
                if keep < len(data):
                    truncated = True
            else:
                truncated = True
        return b''.join(chunks).decode('utf-8', 'replace'), truncated

    # ---- artifacts ---------------------------------------------------------
    async def _register_artifacts(self, execution_id: str, workspace: Path) -> list[dict]:
        """Record the output files a run left, under stable ids of their own.

        Registration is what a download is looked up by, so a caller can only
        fetch what a run actually produced, never a path it composed itself.
        """
        registered = []
        for relative in self._list_workspace_files(workspace):
            path = workspace / relative
            try:
                info = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            registered.append(await self.store.register_artifact(
                execution_id, relative, info.st_size,
                mimetypes.guess_type(relative)[0] or 'application/octet-stream'))
        return registered

    def _list_workspace_files(self, workspace: Path) -> list[str]:
        """Ordinary files a run left, bounded and never through a link."""
        found: list[str] = []
        root = workspace.resolve()
        for path in sorted(workspace.rglob('*')):
            if len(found) >= self.config.max_artifacts:
                break
            if path.is_symlink():
                continue
            try:
                resolved = path.resolve()
                info = os.stat(resolved)
            except OSError:
                continue
            if root not in resolved.parents or not stat.S_ISREG(info.st_mode):
                continue
            found.append(str(resolved.relative_to(root)))
        return found

    @staticmethod
    def _write_control(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
