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
import json
import mimetypes
import os
import re
import signal
import stat
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from len_bot.execution.protocol import (
    OCCUPYING_STATES, ExecutionRecord, ExecutionRequest, ExecutionState, TerminationReport,
)
from len_bot.services.worker_gateway.config import GatewayConfig, WorkerImage
from len_bot.services.worker_gateway.egress_control import CONTROL_RELATIVE_PATH, control_document
from len_bot.services.worker_gateway.egress_proxy import credential_for
from len_bot.services.worker_gateway.store import GatewayStore

_CONTAINER_PREFIX = 'lenbot-x-'
_SAFE_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$')


class GatewayRefusal(ValueError):
    """A request the Gateway refuses before anything is started."""


class ExecutionRunner:
    def __init__(self, config: GatewayConfig, store: GatewayStore,
                 workspaces_root: Path, controls_root: Path, clock=time.time,
                 runtime_path: str | None = None, egress_proxies: dict | None = None):
        self.config = config
        self.store = store
        self.workspaces = workspaces_root
        self.controls = controls_root
        self.clock = clock
        self.runtime_path = runtime_path or config.runtime_path
        # One proxy per proxy policy, started by the service before it serves.
        # A policy whose proxy is absent is refused at acceptance rather than
        # run on a network where the only route out is unchecked.
        self.egress_proxies = dict(egress_proxies or {})
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
        path.mkdir(parents=True, exist_ok=True, mode=0o750)
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
            await self._assert_workspace_available(request)
            if await self.store.count_unfinished() >= self.config.max_concurrent:
                raise GatewayRefusal(
                    f'网关已有 {self.config.max_concurrent} 个未释放执行；本次不排队、不启动，'
                    '请稍后以同一执行 ID 重试')
            try:
                self.config.worker_for(request.image_ref, request.worker_type)
                policy = self.config.policy_for(request.network_policy)
            except KeyError as error:
                raise GatewayRefusal(str(error.args[0])) from None
            if policy.mode == 'proxy':
                # Two independent answers are required before a networked run
                # starts.  The deployment must have built the policy *and*
                # started its proxy; the host must have said this execution is
                # authorized to egress.  Either one missing and the request is
                # refused rather than run offline under a networked policy or
                # run networked because a reference happened to exist.
                if not policy.deployment_verified:
                    raise GatewayRefusal(
                        f'网络策略 {request.network_policy} 尚未完成部署核验；'
                        '未确认 worker 只能到达出口代理前，本次不启动联网执行')
                if request.network_policy not in self.egress_proxies:
                    raise GatewayRefusal(
                        f'网络策略 {request.network_policy} 需要出口代理，但本进程没有为它启动代理；'
                        '本次不启动')
                if not request.egress_authorized:
                    raise GatewayRefusal(
                        f'本次执行选择了需要出口的网络策略 {request.network_policy}，'
                        '但宿主没有给出联网授权；本次不启动')
            if request.input_assets and not request.input_files:
                # Asset ids are provenance only.  The host is the side that
                # reads observations and assets; its exported bytes reach this
                # Gateway as ``input_files``, and a request that names assets
                # without exporting anything is refused rather than silently
                # run with its inputs missing.  When both are present the ids
                # are recorded and never fetched — this Gateway has no media
                # client and no route to one.
                raise GatewayRefusal('本网关不按资产 ID 拉取输入；请由宿主经 input_files 传输字节')
            if request.input_files:
                # Written before the row exists: a refused input never
                # occupies an execution identity, and a duplicate submit finds
                # the same files already on disk.
                self._write_input_files(request)
            try:
                record, created = await self.store.record_execution(request)
            except ValueError as error:
                raise GatewayRefusal(str(error)) from None
            if not created:
                return record, False
            await self.store.append_execution_event(request.execution_id, 'accepted',
                                                    '执行已登记；容器尚未启动')
            self._tasks[request.execution_id] = asyncio.create_task(self._run(request))
            return await self._stored(request.execution_id), True

    def _write_input_files(self, request: ExecutionRequest) -> None:
        """Persist host-exported inputs into this execution's control area.

        ``manifest.json`` keeps the same in-container path the local worker
        used (`/lenbot-control/manifest.json`); every other input lands under
        `/lenbot-control/input/`.  Total decoded bytes stay under the same
        bound the workspace itself has.

        Nothing is published until every input of this request has decoded and
        the total is inside the limit: the bytes are written into this
        execution's own staging area first, and only a request that passes in
        full is moved into place.  A rejected request therefore leaves no
        input file behind, and the cleanup only ever removes this execution's
        own staging files — never another run's data.
        """
        import base64
        import binascii

        decoded: list[tuple[str, bytes]] = []
        total = 0
        for item in request.input_files:
            if item.text is not None:
                data = item.text.encode('utf-8')
            else:
                try:
                    data = base64.b64decode(item.content_base64, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise GatewayRefusal(f'输入文件 {item.name} 不是合法 base64：{error}') from None
            total += len(data)
            if total > self.config.max_workspace_bytes:
                raise GatewayRefusal('输入资料合计超过网关的工作目录字节上限')
            decoded.append((item.name, data))

        control = self.control_directory(request.execution_id)
        staging = self.staging_directory(request.execution_id)
        try:
            for name, data in decoded:
                self._write_control_bytes(staging / name, data)
            for name, _data in decoded:
                if name == 'manifest.json':
                    target = control / name
                else:
                    (control / 'input').mkdir(parents=True, exist_ok=True, mode=0o750)
                    target = control / 'input' / name
                os.replace(staging / name, target)
        finally:
            # Staging is this request's own scratch area; a refusal half way
            # through removes only what this call wrote.
            self._remove_staging(staging)

    def staging_directory(self, execution_id: str) -> Path:
        """This execution's own scratch area for inputs that are not yet live."""
        self._require_safe_name(execution_id, 'execution_id')
        root = self.controls / '_staging'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / execution_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path.resolve()

    @staticmethod
    def _remove_staging(staging: Path) -> None:
        try:
            for entry in staging.iterdir():
                if entry.is_file() and not entry.is_symlink():
                    entry.unlink(missing_ok=True)
            staging.rmdir()
        except OSError:
            pass

    @staticmethod
    def _write_control_bytes(path: Path, content: bytes) -> None:
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

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
        workspace = self.workspace_directory(request.workspace_id)
        try:
            record = await self._stored(execution_id)
            if record.state is ExecutionState.CANCEL_REQUESTED:
                await self._stop(execution_id, container_name, None, reason='启动前已取消，不再创建容器')
                return
            worker = self.config.worker_for(request.image_ref, request.worker_type)
            policy = self.config.policy_for(request.network_policy)
            control = self.control_directory(execution_id)
            self._write_control(control / 'task.py', request.script)
            egress = self._write_egress_control(control, policy, execution_id, request.network_policy)
            denied = self._apply_worker_access(control, workspace, worker)
            if denied:
                # The run cannot read its own script, inputs or workspace, and
                # a permission failure inside the container would be misread as
                # a script bug.  That is a deployment error, so the run stops
                # here with the reason instead of starting and failing later.
                await self._fail(execution_id, 'permissions_insufficient',
                                 '目录权限未生效，无法启动执行：' + '; '.join(denied)[:1800])
                return
            command = self._command(container_name, worker, policy, control, workspace, egress)
            try:
                process = await asyncio.create_subprocess_exec(
                    *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    start_new_session=True)
            except OSError as error:
                await self._fail(execution_id, 'start_failed', f'容器运行时不可用：{error}')
                return
            # The readers start with the process: a container that writes a
            # burst of output before its start is confirmed must not fill the
            # pipe and stall against readers that do not exist yet.
            streams = [asyncio.create_task(self._read_limited(process.stdout)),
                       asyncio.create_task(self._read_limited(process.stderr))]
            current = await self._stored(execution_id)
            if current.state is ExecutionState.CANCEL_REQUESTED:
                await self._stop(execution_id, container_name, process, reason='启动过程中已取消')
                return
            await self.store.append_execution_event(
                execution_id, 'starting', f'容器 {container_name} 已创建，等待运行确认',
                state=ExecutionState.STARTING)
            await self._confirm_started(execution_id, container_name, process, record.deadline_at)
            # Confirmed or not, the run is watched the same way: an unconfirmed
            # start may only mean the runtime was slow to create the container,
            # and the deadline, an external cancellation and the workspace
            # limits must hold for it exactly as for a confirmed one.
            stopped = await self._watch(execution_id, container_name, process, record.deadline_at,
                                        workspace)
            if not stopped:
                await process.wait()
            else:
                await self._collect(process)
            for stream in streams:
                if not stream.done():
                    stream.cancel()
            results = await asyncio.gather(*streams, return_exceptions=True)
            current = await self.store.get_execution(execution_id)
            if stopped or current is None or current.state not in {
                    ExecutionState.STARTING, ExecutionState.RUNNING}:
                # A stop happened before the end state could be recorded.  The
                # process's own output still exists and is still the evidence
                # of what it did: it is written as a fact beside the terminal
                # state instead of being dropped with the stream objects.
                await self._record_stopped_output(execution_id, results)
                return
            await self._record_process_end(execution_id, process, results, workspace)
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(execution_id, container_name, process,
                                            reason='网关正在停止该执行'))
            raise
        except Exception as error:
            await asyncio.shield(self._stop(execution_id, container_name, process,
                                            reason=f'执行器失败，转入停止核对：{error}'))
            current = await self.store.get_execution(execution_id)
            if current is not None and current.state not in {
                    ExecutionState.EXITED, ExecutionState.FAILED,
                    ExecutionState.TERMINATION_CONFIRMED, ExecutionState.TERMINATION_UNCONFIRMED}:
                try:
                    await self._fail(execution_id, 'runner_error', f'执行器失败：{error}')
                except ValueError:
                    pass
        finally:
            for stream in streams:
                if not stream.done():
                    stream.cancel()
            # The run's egress allowance ends with the run.  It is dropped
            # here — a deterministic point that does not depend on how the run
            # ended — so the next execution in this process always starts from
            # its own allowance instead of inheriting bytes it never spent.
            spent = await self.release_egress(request)
            if spent is not None:
                try:
                    await self.store.append_execution_event(
                        execution_id, 'egress_released',
                        f'本次执行的出口用量已释放：上传 {spent.get("bytes_up", 0)} 字节，'
                        f'下载 {spent.get("bytes_down", 0)} 字节，'
                        f'合计 {spent.get("bytes_total", 0)} 字节')
                except ValueError:
                    pass
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

    async def admit_egress(self, policy_name: str, execution_id: str):
        """Whether this execution may still open connections on this policy."""
        record = await self.store.get_execution(execution_id)
        if record is None or record.network_policy != policy_name:
            return None
        request = await self.store.execution_request_of(execution_id)
        if request is None or not request.egress_authorized:
            return None
        if record.state not in {
                ExecutionState.ACCEPTED, ExecutionState.STARTING,
                ExecutionState.RUNNING, ExecutionState.CANCEL_REQUESTED}:
            return None
        return record

    async def release_egress(self, request: ExecutionRequest) -> dict | None:
        """Revoke this execution's new connections and keep the spent count.

        Called once per run from the runner's own cleanup.  The proxy does not
        mint a fresh budget for the same credential afterwards.
        """
        proxy = self.egress_proxies.get(request.network_policy)
        if proxy is None:
            return None
        budget = proxy.revoke(request.execution_id)
        if budget is None:
            return None
        return {'bytes_up': budget.used_request, 'bytes_down': budget.used_response,
                'bytes_total': budget.used_total}

    async def _fail(self, execution_id: str, kind: str, detail: str) -> None:
        await self.store.append_execution_event(execution_id, kind, detail,
                                                state=ExecutionState.FAILED, error=detail)

    @staticmethod
    def _stream_result(results: list, index: int) -> tuple[str, bool]:
        value = results[index]
        return value if isinstance(value, tuple) else ('', False)

    def _command(self, container_name: str, worker: WorkerImage, policy,
                 control: Path, workspace: Path, egress: dict | None) -> list[str]:
        command = [self.runtime_path, 'run', '--rm', '--name', container_name,
                   '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
                   '--user', worker.container_user, '--pids-limit', str(worker.pids_limit),
                   '--memory', worker.memory, '--cpus', worker.cpus,
                   '--tmpfs', '/tmp:rw,noexec,nosuid,size=64m']
        # The egress rule comes from the deployment's policy entry, never from
        # the caller: a request names a policy, and an unbuilt policy is
        # refused before this point.  A proxied policy joins the internal
        # network the policy names, where the egress proxy is the address the
        # container is pointed at and the port is the only one it may reach;
        # whether anything else is reachable there is the operator's network
        # to establish, not something this command line decides.
        network = policy.network if policy.mode == 'proxy' else 'none'
        command += ['--network', network]
        if egress:
            for name, value in egress['env'].items():
                command += ['--env', f'{name}={value}']
        command += ['-v', f'{workspace}:/workspace:rw', '-v', f'{control}:/lenbot-control:ro',
                    '-w', '/workspace', worker.image, 'python', '/lenbot-control/task.py']
        return command

    def egress_environment(self, policy, execution_id: str, policy_name: str) -> dict[str, str] | None:
        """The container's proxy environment, as a mapping with no host paths.

        The proxy URL carries this execution's credential as ``user:password``,
        which urllib / requests / httpx send as ``Proxy-Authorization: Basic``.
        A separate custom header is not a client default and is not used here.
        """
        if policy.mode != 'proxy':
            return None
        credential = credential_for(self.config.token, execution_id, policy_name)
        execution_id, _, mac = credential.partition('.')
        user = quote(execution_id, safe='')
        proxy_url = f'http://{user}:{mac}@{policy.proxy_address}:{policy.proxy_port}'
        bypass = '127.0.0.1,localhost'
        return {'HTTP_PROXY': proxy_url, 'HTTPS_PROXY': proxy_url,
                'http_proxy': proxy_url, 'https_proxy': proxy_url,
                'NO_PROXY': bypass, 'no_proxy': bypass}

    def _write_egress_control(self, control: Path, policy, execution_id: str,
                              policy_name: str) -> dict | None:
        """The read-only egress file, and the environment that points at the proxy.

        An offline policy writes nothing and sets nothing: there is no egress
        rule to describe, and a proxy variable pointing at nothing would be a
        misleading hint rather than a limit.  A proxied policy writes rules
        that are the policy's own, and an HTTP_PROXY URL that already carries
        this execution's credential so ordinary clients authenticate.
        """
        if policy.mode != 'proxy':
            return None
        env = self.egress_environment(policy, execution_id, policy_name)
        document = control_document(policy.rules(), execution_id=execution_id,
                                    proxy_url=env['HTTPS_PROXY'], bypass=env['NO_PROXY'])
        self._write_control(control / CONTROL_RELATIVE_PATH,
                            json.dumps(document, ensure_ascii=False, indent=2))
        return {'env': env}

    async def _confirm_started(self, execution_id: str, container_name: str, process,
                               deadline_at: float) -> bool:
        """Wait until the runtime reports the container actually running.

        "Not found" alone does not mean gone: the runtime may simply not have
        created the container yet.  Only an absent container whose own client
        process has already exited is really over.  Confirmation does not get
        a second time budget: cancel and the original deadline stop this loop.
        """
        for _ in range(self.config.start_confirm_attempts):
            if self.clock() >= deadline_at:
                return False
            current = await self.store.get_execution(execution_id)
            if current is not None and current.state is ExecutionState.CANCEL_REQUESTED:
                return False
            running = await self._inspect_running(container_name)
            if running is True:
                await self.store.append_execution_event(execution_id, 'running',
                                                        '容器已确认运行', state=ExecutionState.RUNNING)
                return True
            if running is False and process.returncode is not None:
                return False
            await asyncio.sleep(min(self.config.start_confirm_interval_seconds,
                                    max(0.05, deadline_at - self.clock())))
        return False

    # ---- watching and stopping --------------------------------------------
    async def _watch(self, execution_id: str, container_name: str, process,
                     deadline_at: float, workspace: Path) -> bool:
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
            over = self._workspace_over_limit(workspace)
            if over:
                await self._stop(execution_id, container_name, process, reason=over)
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
        if current.state in {ExecutionState.EXITED, ExecutionState.FAILED}:
            # The run already ended on its own; there is no end state to
            # rewrite.  The container client is still cleaned up.
            await self._terminate(container_name, process)
            return None
        if current.state is not ExecutionState.CANCEL_REQUESTED:
            await self.store.append_execution_event(execution_id, 'cancel_requested', reason,
                                                    state=ExecutionState.CANCEL_REQUESTED)
        report = await self._terminate(container_name, process)
        if report.status != 'unconfirmed':
            # Outputs are copied while the row still says cancel_requested and
            # therefore still occupies its workspace; only then is the
            # terminal state written.  A next revision claiming the directory
            # can never overlap the copy.
            await self._register_artifacts(execution_id,
                                           self.workspace_directory(current.workspace_id))
        state = (ExecutionState.TERMINATION_CONFIRMED if report.status != 'unconfirmed'
                 else ExecutionState.TERMINATION_UNCONFIRMED)
        await self.store.append_execution_event(
            execution_id, 'termination', f'终止结果：{report.status}；{report.detail}', state=state,
            termination=report, error=None if report.status != 'unconfirmed' else '终止未确认')
        return report

    async def _recheck_unconfirmed(self, execution_id: str) -> ExecutionRecord:
        """Re-inspect an unknown termination; confirm it only on real evidence.

        This is the release path for ``termination_unconfirmed``: a later
        inspection that actually establishes the container is stopped or gone
        moves the run to ``termination_confirmed``, which frees its workspace
        and its capacity slot.  A recheck that still cannot tell keeps the
        original honest answer and the block that goes with it.
        """
        current = await self._stored(execution_id)
        report = await self._terminate(self.container_name(execution_id), None)
        if report.status != 'unconfirmed':
            await self._register_artifacts(execution_id,
                                           self.workspace_directory(current.workspace_id))
            await self.store.append_execution_event(
                execution_id, 'termination', f'复核终止结果：{report.status}；{report.detail}',
                state=ExecutionState.TERMINATION_CONFIRMED, termination=report, error='')
        else:
            await self.store.append_execution_event(
                execution_id, 'termination_recheck', '再次核对容器仍无法确认终止，保留原终态')
        return await self._stored(execution_id)

    async def cancel(self, execution_id: str, reason: str) -> tuple[ExecutionRecord, bool]:
        """Ask for a stop and report how far it got.

        Returns the stored record and whether a stop was attempted.  When a
        watcher task is running in this process the request is recorded and the
        watcher carries it out; when none is (an operator call, or after a
        restart) this call carries it out itself.  Either way ``cancel_requested``
        alone is not a stop: only the termination state says what happened.
        """
        current = await self._stored(execution_id)
        if current.state is ExecutionState.TERMINATION_UNCONFIRMED:
            return await self._recheck_unconfirmed(execution_id), True
        if current.state in {ExecutionState.EXITED, ExecutionState.FAILED,
                             ExecutionState.TERMINATION_CONFIRMED}:
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
            try:
                record = await self.store.get_execution(execution_id)
                if record is None or execution_id in self._tasks:
                    continue
                reason = ('网关重启后发现该执行已超过原期限，按原期限停止'
                          if self.clock() >= record.deadline_at
                          else '网关重启后该执行没有可接续的监视进程，转入停止核对')
                await self._stop(execution_id, self.container_name(execution_id), None, reason=reason)
            except Exception as error:
                try:
                    await self.store.append_execution_event(
                        execution_id, 'sweep_error', f'单条恢复失败，保留阻断：{error}')
                except Exception:
                    pass
        # An unknown termination is also rechecked on restart: the inspection
        # is the same evidence an operator's recheck uses, and a container
        # proven gone releases the workspace instead of blocking it forever.
        for execution_id in await self.store.unconfirmed_terminations():
            try:
                await self._recheck_unconfirmed(execution_id)
            except Exception as error:
                try:
                    await self.store.append_execution_event(
                        execution_id, 'sweep_error', f'未知终止复核失败，保留阻断：{error}')
                except Exception:
                    pass

    # ---- container client helpers -----------------------------------------
    async def _inspect_running(self, container_name: str) -> bool | None:
        """True if running, False if absent or stopped, None if inspect itself failed."""
        code, output = await self._run_bounded(self.runtime_path, 'inspect', '--format',
                                               '{{.State.Running}}', container_name)
        if code is None:
            return None
        if code != 0:
            text = (output or '').lower()
            if 'no such' in text or 'not found' in text:
                return False
            return None
        return output.strip().lower() == 'true'

    async def _terminate(self, container_name: str, process) -> TerminationReport:
        details: list[str] = []
        deadline = self.clock() + self.config.cleanup_timeout_seconds

        def left() -> float:
            # One cleanup budget covers the whole confirmation, so each step
            # gets what remains of it rather than a full budget of its own.
            return max(0.1, deadline - self.clock())

        code, output = await self._run_bounded(self.runtime_path, 'kill', container_name,
                                               timeout=left())
        if code is None:
            details.append(output or 'container kill did not confirm')
        elif code != 0:
            details.append(output or 'container kill failed')
        if process is not None:
            self._reap(process)
            await self._collect(process, timeout=left())
        status = 'unconfirmed'
        if self.clock() < deadline:
            code, output = await self._run_bounded(self.runtime_path, 'rm', '-f', container_name,
                                                   timeout=left())
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
                self.runtime_path, 'inspect', '--format', '{{.State.Running}}', container_name,
                timeout=left())
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

    async def _run_bounded(self, *argv: str, timeout: float | None = None) -> tuple[int | None, str]:
        """Run one bounded runtime command and always reap its own client."""
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                start_new_session=True)
        except OSError as error:
            return None, str(error)
        try:
            await asyncio.wait_for(process.wait(),
                                   timeout=timeout if timeout is not None
                                   else self.config.cleanup_timeout_seconds)
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

    async def _collect(self, process, timeout: float | None = None) -> None:
        """Reap a client process; its own output tasks read the pipes to EOF."""
        try:
            await asyncio.shield(asyncio.wait_for(
                process.wait(), timeout=timeout if timeout is not None
                else self.config.cleanup_timeout_seconds))
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

    async def _assert_workspace_available(self, request: ExecutionRequest) -> None:
        owners = await self.store.executions_for_workspace(request.workspace_id)
        for other in owners:
            if other.execution_id == request.execution_id:
                continue
            if other.scene_id != request.scene_id or other.job_id != request.job_id:
                raise GatewayRefusal(
                    f'工作区 {request.workspace_id} 已属于工作 {other.job_id}，不能借给其他工作')
            stored = await self.store.execution_request_of(other.execution_id)
            if stored is not None and stored.initiator != request.initiator:
                raise GatewayRefusal(f'工作区 {request.workspace_id} 已绑定其他发起者，不能改写归属')
            if other.job_revision != request.job_revision and other.state in OCCUPYING_STATES:
                raise GatewayRefusal(
                    f'工作区 {request.workspace_id} 仍被修订 {other.job_revision} 占用，旧修订只可观察')
            if other.state in OCCUPYING_STATES:
                raise GatewayRefusal(
                    f'工作区 {request.workspace_id} 仍有未释放执行 {other.execution_id}（{other.state.value}）')

    def _apply_worker_access(self, control: Path, workspace: Path, worker) -> list[str]:
        """Give the worker GID its access; report what could not be established.

        A target that already has the required owner, group and mode bits is
        left alone — repeating a chown that would change nothing is not what
        makes the run work, and it is exactly what fails on a deployment whose
        Gateway process may not chgrp to the worker's group.  A target that
        genuinely lacks the access is fixed here, then re-read: what decides
        the answer is the access that actually exists afterwards, not whether a
        chown call returned an error.  An unresolved target is returned so the
        caller can stop the run as a deployment problem — the container would
        otherwise fail inside the user's script, and a real permission problem
        would be misread as a script bug.
        """
        _uid, gid = (int(part) for part in worker.container_user.split(':'))
        script = control / 'task.py'
        targets: list[tuple[Path, int]] = [(control, 0o750), (workspace, 0o770), (script, 0o640)]
        # The egress file is written before this point, because the run's
        # command line carries the proxy address that file describes.
        for name in ('manifest.json', CONTROL_RELATIVE_PATH):
            extra = control / name
            if extra.exists():
                targets.append((extra, 0o640))
        inputs = control / 'input'
        if inputs.is_dir():
            targets.append((inputs, 0o750))
            targets.extend((entry, 0o640) for entry in sorted(inputs.iterdir())
                           if entry.is_file() and not entry.is_symlink())
        unresolved: list[str] = []
        for path, mode in targets:
            if not path.exists():
                continue
            try:
                os.chmod(path, mode)
            except OSError:
                pass
            try:
                os.chown(path, os.getuid(), gid)
            except OSError:
                pass
            try:
                info = path.stat()
            except OSError as error:
                unresolved.append(f'无法读取 {path.name} 的权限：{error}')
                continue
            # The worker's group must be able to enter a directory and read a
            # file; the group bits are what the container's user relies on.
            needed = 0o050 if stat.S_ISDIR(info.st_mode) else 0o040
            if stat.S_IMODE(info.st_mode) & needed != needed:
                unresolved.append(
                    f'{path.name} 的权限 {oct(stat.S_IMODE(info.st_mode))} 不允许 worker 组访问')
        return unresolved

    async def _record_process_end(self, execution_id, process, results, workspace: Path) -> None:
        stdout, stdout_truncated = self._stream_result(results, 0)
        stderr, stderr_truncated = self._stream_result(results, 1)
        current = await self.store.get_execution(execution_id)
        if current is None or current.state in {
                ExecutionState.TERMINATION_CONFIRMED, ExecutionState.TERMINATION_UNCONFIRMED,
                ExecutionState.EXITED, ExecutionState.FAILED}:
            return
        if current.state is ExecutionState.CANCEL_REQUESTED:
            await self._stop(execution_id, self.container_name(execution_id), process,
                             reason='取消后收集到进程结果')
            return
        state = ExecutionState.EXITED if process.returncode == 0 else ExecutionState.FAILED
        # Outputs are copied while this row still occupies its workspace; only
        # then is the end state written.  Written the other way around, a next
        # revision could claim the directory mid-copy, and a crash between the
        # two would leave a terminal run whose artifacts nothing re-collects.
        await self._register_artifacts(execution_id, workspace)
        await self.store.append_execution_event(
            execution_id, 'exited' if state is ExecutionState.EXITED else 'failed',
            f'进程以返回码 {process.returncode} 结束', state=state,
            returncode=process.returncode,
            error=None if state is ExecutionState.EXITED else '进程以非零返回码结束',
            stdout=stdout, stderr=stderr, stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated)

    async def _record_stopped_output(self, execution_id: str, results: list) -> None:
        """Keep the limited output a stopped run already produced.

        A cancelled or deadline-stopped execution has a terminal state already,
        which must not be rewritten.  What the process had already written is a
        separate fact and is appended as one, so a reader of a stopped run can
        still see why it stopped instead of finding empty streams.
        """
        stdout, stdout_truncated = self._stream_result(results, 0)
        stderr, stderr_truncated = self._stream_result(results, 1)
        if not stdout and not stderr and not stdout_truncated and not stderr_truncated:
            return
        try:
            await self.store.append_execution_event(
                execution_id, 'final_output', '停止前已取得的有限输出',
                stdout=stdout, stderr=stderr,
                stdout_truncated=stdout_truncated, stderr_truncated=stderr_truncated)
        except (ValueError, OSError):
            # The row is already in a terminal state that cannot carry this
            # fact; the log is not lost silently, but it must not turn a real
            # stop into an executor failure either.
            pass

    def _workspace_over_limit(self, workspace: Path) -> str | None:
        files = total = 0
        for _relative, size in self._iter_workspace_files(workspace):
            files += 1
            total += size
            if files > self.config.max_workspace_files:
                return f'工作目录文件数超过上限 {self.config.max_workspace_files}，已停止'
            if total > self.config.max_workspace_bytes:
                return f'工作目录字节数超过上限 {self.config.max_workspace_bytes}，已停止'
        return None

    def _iter_workspace_files(self, workspace: Path):
        root = workspace.resolve()
        stack = [root]
        seen = 0
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        try:
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        try:
                            rel = str(Path(entry.path).relative_to(root))
                        except ValueError:
                            continue
                        yield rel, info.st_size
                        seen += 1
                        if seen >= max(self.config.max_artifacts, self.config.max_workspace_files) + 1:
                            return
            except OSError:
                continue

    def artifacts_store(self, execution_id: str) -> Path:
        self._require_safe_name(execution_id, 'execution_id')
        root = self.controls / '_artifacts'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / execution_id
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        return path.resolve()

    def open_stored_artifact(self, execution_id: str, artifact_id: str, *, offset: int = 0) -> int:
        """One stored artifact's descriptor, positioned at a byte offset.

        The position is a real seek on the file itself, so a reader asking for
        a page of a large file does not have to pull the whole thing through
        the process that owns it first.
        """
        self._require_safe_name(execution_id, 'execution_id')
        if not re.fullmatch(r'^[0-9a-f]{32}$', artifact_id or ''):
            raise GatewayRefusal('产物标识不合法')
        if offset < 0:
            raise GatewayRefusal('产物偏移量不能为负')
        directory = self.artifacts_store(execution_id)
        dir_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fd = os.open(artifact_id, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        finally:
            os.close(dir_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            raise GatewayRefusal('产物不是普通文件')
        if offset:
            try:
                os.lseek(fd, offset, os.SEEK_SET)
            except OSError:
                os.close(fd)
                raise GatewayRefusal('产物偏移量超出文件范围') from None
        return fd

    def _open_workspace_source(self, workspace: Path, relative: str) -> int:
        """Open one scanned workspace file without following any symlink.

        ``O_NOFOLLOW`` guards only the final component, so every directory on
        the way is opened the same way against its parent's descriptor; a
        component swapped for a link after the scan is refused instead of
        being read through to somewhere outside the workspace.
        """
        parts = Path(relative).parts
        if not parts or any(part in ('..', '/', '') for part in parts):
            raise GatewayRefusal('产物路径不合法')
        fd = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for part in parts[:-1]:
                fd_next = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = fd_next
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)

    def _copy_regular_file(self, workspace: Path, relative: str, destination: Path,
                           *, size_limit: int) -> None:
        src = self._open_workspace_source(workspace, relative)
        try:
            info = os.fstat(src)
            if not stat.S_ISREG(info.st_mode):
                raise GatewayRefusal('产物不是普通文件')
            if info.st_size > size_limit:
                raise GatewayRefusal('产物超过可保存字节上限')
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            dst = os.open(destination, flags, 0o600)
            try:
                copied = 0
                while True:
                    chunk = os.read(src, 65536)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > size_limit:
                        raise GatewayRefusal('产物超过可保存字节上限')
                    os.write(dst, chunk)
            finally:
                os.close(dst)
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        finally:
            os.close(src)

    # ---- artifacts ---------------------------------------------------------
    async def _register_artifacts(self, execution_id: str, workspace: Path) -> list[dict]:
        registered = []
        store_dir = self.artifacts_store(execution_id)
        for relative, size in self._iter_workspace_files(workspace):
            if len(registered) >= self.config.max_artifacts:
                break
            over = self._workspace_over_limit(workspace)
            if over:
                await self.store.append_execution_event(execution_id, 'workspace_limit', over)
                break
            # The id is derived from the execution and the path, so a copy
            # that crashed before its row can be finished later under the same
            # name; and the row is only written once the bytes are safely in
            # the immutable store — a registered artifact is a downloadable
            # one, never a promise.
            artifact_id = uuid.uuid5(uuid.NAMESPACE_URL,
                                     f'lenbot-artifact:{execution_id}:{relative}').hex
            stored = store_dir / artifact_id
            if not stored.exists():
                try:
                    self._copy_regular_file(workspace, relative, stored,
                                            size_limit=self.config.max_workspace_bytes)
                except (OSError, GatewayRefusal):
                    continue
            item = await self.store.register_artifact(
                execution_id, relative, size,
                mimetypes.guess_type(relative)[0] or 'application/octet-stream',
                artifact_id=artifact_id)
            registered.append(item)
        return registered

    def _list_workspace_files(self, workspace: Path) -> list[str]:
        found: list[str] = []
        for relative, _size in self._iter_workspace_files(workspace):
            if len(found) >= self.config.max_artifacts:
                break
            found.append(relative)
        return found

    @staticmethod
    def _write_control(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
        temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
