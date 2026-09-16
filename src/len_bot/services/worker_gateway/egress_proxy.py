"""The Gateway's egress proxy: the layer that decides where a connection goes.

The worker container is given an isolated network on which this proxy is the
only reachable address.  A script may therefore construct any URL it likes and
even ignore ``HTTP_PROXY`` entirely: its traffic still arrives here, and here
it is checked against the deployment's rules — the destination host, its port,
the class it belongs to, and *every* address it resolves to.  The proxy then
connects to the numeric address it resolved and admitted, so the name is
resolved once, by the side that enforces the rule.

That is why the environment variables the runner also sets are a convenience
and not the enforcement, and why an application-layer URL check inside the
script is the inner layer only: neither is what stops an unreviewed connect.

Everything the proxy decides is written down as one line per connection: the
execution it belonged to, the host, the port, the class, the outcome and the
byte counts in both directions.  Headers and bodies are never logged — a
destination, a status and a size are the facts an operator needs, and a Cookie
or an authorization header is not.

A ``CONNECT`` tunnel is opaque once established (that is what TLS means here),
so the proxy's control there is the destination check; inside the tunnel it can
only count bytes.  A cleartext ``Upgrade: websocket`` request is refused unless
the deployment turned websockets on, and that opacity is recorded as a limit
rather than described as inspection.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time

from len_bot.execution.protocol import ExecutionState
from len_bot.services.worker_gateway.egress_policy import (
    Budget, EgressBlocked, EgressRules, parse_target,
)

logger = logging.getLogger('len_bot.worker_gateway.egress')

# The credential a worker presents to the proxy is derived from the Gateway's
# own service token, so no second secret has to be distributed.  The label
# keeps this use of the token apart from the token's own use as a bearer
# credential, and the execution id travels inside the credential because the
# proxy has to attribute a connection without holding per-run state.
CREDENTIAL_LABEL = b'lenbot-egress-v1:'

# A request head is read up to this many bytes before it is refused; a head
# that needs more than that is not a request this proxy is going to parse.
MAX_HEAD_BYTES = 32_768

# Hop-by-hop headers are consumed by the proxy itself and never forwarded.
_HOP_BY_HOP = frozenset({
    'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
    'te', 'trailer', 'transfer-encoding', 'upgrade', 'proxy-connection',
})

_FORWARD_METHODS = frozenset({'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'})


def credential_for(token: str, execution_id: str, policy_name: str) -> str:
    """The one credential a run's container presents, bound to one policy.

    The MAC covers both the execution and the policy name, so a credential
    minted for one egress proxy cannot be replayed against another policy
    that happens to share this Gateway's service token.
    """
    mac = hmac.new(token.encode('utf-8'),
                   CREDENTIAL_LABEL + policy_name.encode('utf-8') + b':' + execution_id.encode('utf-8'),
                   hashlib.sha256).hexdigest()
    return f'{execution_id}.{mac}'


def execution_of(token: str, credential: str, policy_name: str) -> str | None:
    """The execution a presented credential belongs to, or ``None``.

    Integrity of the MAC is not admission: the caller still has to check that
    this execution exists, is still running, and was authorized for *this*
    policy.  A missing or foreign credential is refused rather than treated as
    an anonymous allowed caller.
    """
    execution_id, _, mac = (credential or '').strip().partition('.')
    if not execution_id or not mac or len(execution_id) > 64:
        return None
    expected = hmac.new(token.encode('utf-8'),
                        CREDENTIAL_LABEL + policy_name.encode('utf-8') + b':' + execution_id.encode('utf-8'),
                        hashlib.sha256).hexdigest()
    return execution_id if hmac.compare_digest(mac, expected) else None


def _credential_from_headers(headers: dict[str, str], token: str, policy_name: str) -> str | None:
    """Bearer or HTTP Basic proxy credentials, as ordinary clients send them.

    The container's ``HTTP_PROXY`` URL carries ``user:password``; urllib,
    requests and httpx turn that into ``Proxy-Authorization: Basic``.  The
    Bearer form is the same execution_id.mac pair for callers that set the
    header themselves.  Either way the MAC is bound to this policy.
    """
    raw = headers.get('proxy-authorization', '')
    if raw.lower().startswith('bearer '):
        return execution_of(token, raw[7:].strip(), policy_name)
    if raw.lower().startswith('basic '):
        try:
            decoded = base64.b64decode(raw[6:].strip(), validate=True).decode('ascii')
        except (ValueError, UnicodeDecodeError):
            return None
        user, separator, password = decoded.partition(':')
        if not separator:
            return None
        return execution_of(token, f'{user}.{password}', policy_name)
    return None


_ACTIVE_EGRESS = frozenset({
    ExecutionState.ACCEPTED, ExecutionState.STARTING,
    ExecutionState.RUNNING, ExecutionState.CANCEL_REQUESTED,
})


class EgressLog:
    """One JSON line per connection attempt, and nothing else."""

    def __init__(self, path, clock=time.time):
        self.path = path
        self.clock = clock
        self._lock = asyncio.Lock()

    async def write(self, record: dict) -> None:
        if self.path is None:
            return
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        async with self._lock:
            await asyncio.to_thread(self._append, line)

    def _append(self, line: str) -> None:
        try:
            with open(self.path, 'a', encoding='utf-8') as stream:
                stream.write(line + '\n')
        except OSError as error:  # a log failure is reported, never hidden
            logger.error('出口记录写入失败：%s', error)


class EgressProxy:
    """A destination-checking HTTP proxy for one deployment's egress rule set."""

    def __init__(self, rules: EgressRules, token: str, *, policy_name: str,
                 admit=None, log_path=None,
                 listen_host: str = '0.0.0.0', listen_port: int = 8799, clock=time.time):
        self.rules = rules
        self.token = token
        self.policy_name = policy_name
        self.admit = admit
        self.log = EgressLog(log_path, clock=clock)
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.clock = clock
        self._server: asyncio.AbstractServer | None = None
        self._tasks: set[asyncio.Task] = set()
        self._writers: dict[str, list[asyncio.StreamWriter]] = {}
        self._revoked: set[str] = set()
        # One allowance per execution, not per connection.  Revoking a run
        # keeps this count so a later connect cannot mint a fresh budget.
        self._budgets: dict[str, Budget] = {}

    def budget_for(self, execution_id: str) -> Budget:
        if execution_id in self._revoked:
            raise EgressBlocked('本次执行的出口资格已撤销，不能再开新连接')
        budget = self._budgets.get(execution_id)
        if budget is None:
            budget = Budget.for_rules(self.rules)
            self._budgets[execution_id] = budget
        return budget

    def revoke(self, execution_id: str) -> Budget | None:
        """Stop new connections, close existing ones, keep the spent count.

        Dropping the counter would let a still-running container present the
        same credential and receive a fresh allowance.  The spent budget stays
        so a later connect is refused rather than reset.
        """
        self._revoked.add(execution_id)
        for writer in self._writers.pop(execution_id, []):
            try:
                writer.close()
            except OSError:
                pass
        return self._budgets.get(execution_id)

    def release(self, execution_id: str) -> Budget | None:
        """Compatibility name: revoke eligibility, do not mint a new budget."""
        return self.revoke(execution_id)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._on_client, self.listen_host, self.listen_port)

    def _on_client(self, reader, writer):
        task = asyncio.create_task(self._client(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writers in self._writers.values():
            for writer in writers:
                try:
                    writer.close()
                except OSError:
                    pass
        self._writers.clear()
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- one client connection --------------------------------------------
    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        started = self.clock()
        record = {'at': started, 'execution_id': None, 'host': None, 'port': None,
                  'class': None, 'scheme': None, 'method': None, 'decision': 'blocked',
                  'status': None, 'bytes_up': 0, 'bytes_down': 0, 'reason': '', 'detail': ''}
        try:
            head = await asyncio.wait_for(self._read_head(reader), timeout=30)
            if head is None:
                record['reason'] = 'unreadable_head'
                record['detail'] = '请求头无法解析或过长'
                await self._refuse(writer, 400, record['detail'])
                return
            method, target, version, headers = head
            record['method'] = method
            execution_id = _credential_from_headers(headers, self.token, self.policy_name)
            if execution_id is None:
                record['reason'] = 'proxy_credential'
                record['detail'] = '缺少或不属于本网关的出口凭据'
                await self._refuse(writer, 407, record['detail'])
                return
            record['execution_id'] = execution_id
            if execution_id in self._revoked:
                record['reason'] = 'revoked'
                record['detail'] = '本次执行的出口资格已撤销'
                await self._refuse(writer, 403, record['detail'])
                return
            admitted = await self._admit_execution(execution_id)
            if admitted is None:
                record['reason'] = 'execution'
                record['detail'] = '出口凭据不属于当前获准的执行，或该执行已结束'
                await self._refuse(writer, 403, record['detail'])
                return
            self._writers.setdefault(execution_id, []).append(writer)
            remaining = max(0.1, admitted.deadline_at - self.clock())
            if method == 'CONNECT':
                await asyncio.wait_for(self._connect_tunnel(reader, writer, target, record),
                                       timeout=remaining)
            elif method in _FORWARD_METHODS:
                await asyncio.wait_for(
                    self._forward(reader, writer, method, target, version, headers, record),
                    timeout=remaining)
            else:
                record['reason'] = 'method'
                record['detail'] = f'出口只转发普通 HTTP 方法，不转发 {method}'
                await self._refuse(writer, 405, record['detail'])
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            record['reason'] = record['reason'] or 'client_gone'
            record['detail'] = record['detail'] or '客户端在出口处理期间断开'
        except asyncio.TimeoutError:
            record['reason'] = record['reason'] or 'timeout'
            record['detail'] = record['detail'] or '出口处理超过本次执行期限'
        except Exception as error:  # a proxy bug is recorded, not silently dropped
            record['reason'] = record['reason'] or 'proxy_error'
            record['detail'] = str(error)[:300]
            logger.exception('出口代理处理失败')
        finally:
            execution_id = record.get('execution_id')
            if execution_id:
                held = self._writers.get(execution_id) or []
                self._writers[execution_id] = [item for item in held if item is not writer]
            record['duration_ms'] = round((self.clock() - started) * 1000, 1)
            await self.log.write(record)
            try:
                writer.close()
            except OSError:
                pass

    async def _admit_execution(self, execution_id: str):
        """The current execution this credential may still speak for, or None."""
        if self.admit is None:
            return None
        record = await self.admit(execution_id)
        if record is None or record.state not in _ACTIVE_EGRESS:
            return None
        if record.network_policy != self.policy_name:
            return None
        return record

    async def _refuse(self, writer, status: int, reason: str) -> None:
        body = ('出口拒绝：' + reason).encode('utf-8')
        writer.write(b'HTTP/1.1 %d %s\r\nContent-Type: text/plain; charset=utf-8\r\n'
                     b'Content-Length: %d\r\nConnection: close\r\n\r\n'
                     % (status, b'Forbidden' if status in {400, 403, 405} else b'Proxy Error',
                        len(body)))
        writer.write(body)
        await writer.drain()

    async def _admit(self, target: str, default_port: int, record: dict) -> tuple[str, int, str] | None:
        """Check a destination, or write the refusal into the record and return None."""
        try:
            host, port, scheme = parse_target(target, default_port)
            name = self.rules.check_target(host, port)
            addresses = await self.rules.resolve_async(host, port)
        except EgressBlocked as error:
            record['reason'] = 'destination'
            record['detail'] = str(error)
            return None
        record.update(host=host, port=port, scheme=scheme, **{'class': name})
        return addresses[0], port, scheme

    async def _connect_tunnel(self, reader, writer, target: str, record: dict) -> None:
        """An opaque tunnel to one admitted destination, counted both ways."""
        admitted = await self._admit(target, 443, record)
        if admitted is None:
            await self._refuse(writer, 403, record['detail'])
            return
        address, port, _scheme = admitted
        try:
            budget = self.budget_for(record['execution_id'])
        except EgressBlocked as error:
            record.update(reason='revoked', detail=str(error))
            await self._refuse(writer, 403, record['detail'])
            return
        try:
            origin = await asyncio.open_connection(address, port)
        except OSError as error:
            record['reason'] = 'connect'
            record['detail'] = f'连接目的主机失败：{error}'
            await self._refuse(writer, 502, record['detail'])
            return
        writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        await writer.drain()
        budgets = {'up': budget.charge_request, 'down': budget.charge_response}
        try:
            up, down = await self._pump_both_ways(reader, writer, *origin, budgets, record)
        finally:
            origin[1].close()
        record.update(decision='allowed' if record['reason'] != 'budget' else 'blocked',
                      status=200, bytes_up=up, bytes_down=down)

    async def _forward(self, reader, writer, method, target, version, headers, record) -> None:
        """A plain HTTP request: checked, forwarded, and counted as it streams."""
        encoding = (headers.get('transfer-encoding') or '').lower()
        if encoding and encoding != 'identity':
            record.update(reason='transfer_encoding',
                          detail=f'出口不转发 Transfer-Encoding: {encoding}')
            await self._refuse(writer, 403, record['detail'])
            return
        try:
            body_length = int(headers.get('content-length') or 0)
        except ValueError:
            record.update(reason='content_length', detail='Content-Length 不是数字')
            await self._refuse(writer, 400, record['detail'])
            return
        if body_length < 0:
            record.update(reason='content_length', detail='Content-Length 不能为负')
            await self._refuse(writer, 400, record['detail'])
            return
        admitted = await self._admit(target, 80, record)
        if admitted is None:
            await self._refuse(writer, 403, record['detail'])
            return
        if 'upgrade' in headers and not self.rules.allow_websockets:
            record.update(reason='websocket', detail='本出口未开启 WebSocket 转发')
            await self._refuse(writer, 403, record['detail'])
            return
        address, port, scheme = admitted
        try:
            budget = self.budget_for(record['execution_id'])
        except EgressBlocked as error:
            record.update(reason='revoked', detail=str(error))
            await self._refuse(writer, 403, record['detail'])
            return
        head = self._origin_head(method, target, version, headers, scheme, record['host'])
        try:
            budget.charge_request(len(head) + body_length)
        except EgressBlocked as error:
            record.update(reason='budget', detail=str(error))
            await self._refuse(writer, 403, record['detail'])
            return
        try:
            origin_reader, origin_writer = await asyncio.open_connection(address, port)
        except OSError as error:
            record['reason'] = 'connect'
            record['detail'] = f'连接目的主机失败：{error}'
            await self._refuse(writer, 502, record['detail'])
            return
        try:
            origin_writer.write(head)
            if body_length:
                remaining = body_length
                while remaining > 0:
                    chunk = await reader.read(min(65_536, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    origin_writer.write(chunk)
            await origin_writer.drain()
            status, down = await self._relay_response(
                origin_reader, writer, budget, record, skip_body=(method == 'HEAD'))
            record.update(decision='allowed' if record['reason'] != 'budget' else 'blocked',
                          status=status, bytes_down=down, bytes_up=len(head) + body_length)
        finally:
            origin_writer.close()

    @staticmethod
    def _origin_head(method: str, target: str, version: str, headers, scheme: str, host: str) -> bytes:
        """The request line and headers as the origin request should carry them.

        Absolute-form is rewritten to origin-form, hop-by-hop headers and the
        client's own Host are dropped, and the destination's Host is written
        from the host this proxy admitted.  A header block that will not encode
        as latin-1 is the caller's problem and is answered as a normal refusal.
        """
        if target.startswith(('http://', 'https://')):
            path = target.split('://', 1)[1]
            path = path[path.index('/'):] if '/' in path else '/'
        else:
            path = target if target.startswith('/') else '/'
        head = f'{method} {path} {version}\r\n'.encode('latin-1')
        for key, value in headers.items():
            if key in _HOP_BY_HOP or key == 'host':
                continue
            head += f'{key}: {value}\r\n'.encode('latin-1')
        return head + f'Host: {host}\r\nConnection: close\r\n\r\n'.encode('latin-1')

    async def _relay_response(self, origin_reader, writer, budget: Budget, record,
                              skip_body: bool = False) -> tuple[int | None, int]:
        """Stream the response back, counting bytes and stopping at the limit."""
        line = await origin_reader.readline()
        try:
            status: int | None = int(line.split(b' ')[1])
        except (IndexError, ValueError):
            status = None
        writer.write(line)
        header_bytes = len(line)
        while True:
            header = await origin_reader.readline()
            header_bytes += len(header)
            writer.write(header)
            if header in {b'\r\n', b'\n', b''}:
                break
            if header_bytes > MAX_HEAD_BYTES:
                record.update(reason='response_head', detail='响应头超过可处理长度')
                return status, 0
        await writer.drain()
        total = 0
        try:
            budget.charge_response(header_bytes)
        except EgressBlocked as error:
            record.update(reason='budget', detail=str(error))
            return status, total
        if skip_body:
            return status, total
        while True:
            chunk = await origin_reader.read(65_536)
            if not chunk:
                break
            try:
                budget.charge_response(len(chunk))
            except EgressBlocked as error:
                record.update(reason='budget', detail=str(error))
                break
            total += len(chunk)
            writer.write(chunk)
            await writer.drain()
        return status, total

    @staticmethod
    async def _pump_both_ways(client_reader, client_writer, origin_reader, origin_writer,
                              budgets: dict, record) -> tuple[int, int]:
        """Copy both directions until one ends, charging each side's budget.

        A refusal here is a budget refusal, so it stops the connection instead
        of being raised as a proxy failure; the counts returned are what was
        really forwarded, which is what the record states.
        """
        totals = {'up': 0, 'down': 0}

        async def pump(source, sink, side: str) -> None:
            while True:
                data = await source.read(65_536)
                if not data:
                    return
                budgets[side](len(data))
                totals[side] += len(data)
                sink.write(data)
                await sink.drain()

        tasks = [asyncio.create_task(pump(client_reader, origin_writer, 'up')),
                 asyncio.create_task(pump(origin_reader, client_writer, 'down'))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, EgressBlocked):
                record['reason'] = record['reason'] or 'budget'
                record['detail'] = record['detail'] or str(result)
        return totals['up'], totals['down']

    # ---- request head ------------------------------------------------------
    async def _read_head(self, reader) -> tuple[str, str, str, dict[str, str]] | None:
        """Read one request head, bounded, without reading the body.

        Header names are lower-cased and repeated values are joined, so the
        rest of the proxy looks one up by name.  The head is never logged: only
        the fields the record declares are.
        """
        try:
            raw = await reader.readuntil(b'\r\n\r\n')
        except asyncio.LimitOverrunError:
            return None
        if len(raw) > MAX_HEAD_BYTES:
            return None
        lines = raw.decode('latin-1').split('\r\n')
        parts = lines[0].split(' ')
        if len(parts) != 3:
            return None
        method, target, version = parts[0].upper(), parts[1], parts[2]
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line or ':' not in line:
                continue
            key, _, value = line.partition(':')
            key = key.strip().lower()
            headers[key] = f'{headers[key]}, {value.strip()}' if key in headers else value.strip()
        return method, target, version, headers
