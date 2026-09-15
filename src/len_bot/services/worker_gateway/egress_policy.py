"""The one egress rule set: destination classes, address classes and budgets.

Two layers enforce outbound traffic, and both read *this* file, so the rules
exist once:

* the Gateway's egress proxy imports it and is the layer that actually decides
  where a connection may go.  It is the authoritative one: the worker's only
  route off its internal network is that proxy;
* each execution copies this same file, byte for byte, into its read-only
  control area, where a script can import it to check a destination *before*
  connecting — so an ordinary refusal reads as "this target is not in the
  allowed class", not as an unexplained socket error — and to keep its own
  byte budget.

The inner copy is a convenience, never the guarantee: a script may connect
without asking it.  What a script cannot do is reach a destination the proxy
does not forward, and the proxy resolves the name itself, checks *every*
resolved address against the classes below, and connects to the numeric
address it just checked.  A hostname pre-check followed by somebody else's
independent resolution would not bind anything.

This module imports nothing from this project and nothing outside the standard
library, because it is also the file that runs inside the execution container.
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit

# The two ports a public research read normally needs.  A deployment may widen
# this list, and every other port is refused rather than forwarded.
DEFAULT_PORTS = (80, 443)

# Classes a destination host may belong to.  The split is the plan's: a
# business domain is a site this deployment reads from, a resource domain is
# where that site's own assets live.  Neither one is a general-purpose
# forwarding allowance, and an entry here is an exact host or a ``*.suffix``
# pattern, never a bare shared suffix with an implicit wildcard.
HOST_CLASSES = ('business', 'resource')

# Address prefixes that can carry an IPv4 destination inside an IPv6 literal:
# a v4-mapped address, a 6to4 address, and the well-known NAT64 prefix.  A
# translated literal must be judged by the address it actually reaches, not by
# its outer IPv6 form.
_NAT64_PREFIX = ipaddress.ip_network('64:ff9b::/96')
_SIX_TO_FOUR_PREFIX = ipaddress.ip_network('2002::/16')
_PUBLIC_V6 = ipaddress.ip_network('2000::/3')


class EgressBlocked(ValueError):
    """A destination or a transfer this policy does not allow.

    The message is the operator's and the script's explanation, and it is the
    same text in both layers: the proxy's refusal body and the in-container
    exception carry the reason the policy actually recorded.
    """


def _ipv4_from_nat64(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(address.packed[-4:])


def _ipv4_from_six_to_four(address: ipaddress.IPv6Address) -> ipaddress.IPv4Address:
    return ipaddress.IPv4Address(address.packed[2:6])


def blocked_address_reason(address: str) -> str | None:
    """Why this numeric address may not be the destination, or ``None``.

    The check is deliberately positive as well as negative.  Blocking the
    private and special ranges is not enough on its own: an address that is
    merely unclassifiable — one that is not a globally routable unicast
    address — is refused too, so a range this code has never heard of fails
    closed instead of being forwarded by default.
    """
    text = address.split('%', 1)[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return f'{address} 不是可判断的数字地址'

    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            # ::ffff:10.0.0.1 is a private IPv4 destination written as IPv6.
            return blocked_address_reason(str(ip.ipv4_mapped))
        if ip in _NAT64_PREFIX:
            return blocked_address_reason(str(_ipv4_from_nat64(ip)))
        if ip in _SIX_TO_FOUR_PREFIX:
            return blocked_address_reason(str(_ipv4_from_six_to_four(ip)))
        if ip.is_loopback:
            return '环回地址'
        if ip.is_link_local:
            return '链路本地地址'
        if ip.is_multicast:
            return '组播地址'
        if ip.is_unspecified:
            return '未指定地址'
        if ip.is_private or ip.is_reserved:
            return '私有或保留地址'
        if ip not in _PUBLIC_V6:
            return '不是公网可路由的单播地址'
        return None

    if ip.is_loopback:
        return '环回地址'
    if ip.is_link_local:
        return '链路本地地址'
    if ip.is_multicast:
        return '组播地址'
    if ip.is_unspecified:
        return '未指定地址'
    if ip.is_private or ip.is_reserved:
        return '私有或保留地址'
    if not ip.is_global:
        return '不是公网可路由的单播地址'
    return None


def parse_target(value: str, default_port: int = 443) -> tuple[str, int, str]:
    """``scheme://host[:port]/...`` or a CONNECT authority as host, port, scheme.

    Both forms come off the wire, so neither is trusted: a target carrying
    user information (``user:pass@host``) is refused rather than silently
    stripped, and the port must be a number in range.  An IPv6 literal keeps
    its brackets off here and is judged as an address.
    """
    text = (value or '').strip()
    if not text:
        raise EgressBlocked('目标为空')
    scheme = 'https'
    if '://' in text:
        try:
            parsed = urlsplit(text)
            port = parsed.port
        except ValueError as error:
            raise EgressBlocked(f'目标端口不合法：{error}') from None
        if parsed.scheme not in {'http', 'https'}:
            raise EgressBlocked(f'不支持的协议 {parsed.scheme!r}：出口只转发 http 与 https')
        if parsed.username or parsed.password:
            raise EgressBlocked('目标带有用户信息：出口不转发嵌入凭据的目标')
        host, scheme = parsed.hostname or '', parsed.scheme
        if not host:
            raise EgressBlocked('目标缺少主机名')
        if port is None:
            port = default_port if scheme == 'https' else 80
        return host.lower().strip('.'), port, scheme
    # CONNECT authority form, as an authority rather than a path.
    try:
        parsed = urlsplit('//' + text)
        port = parsed.port
    except ValueError as error:
        raise EgressBlocked(f'目标不合法：{error}') from None
    if parsed.username or parsed.password:
        raise EgressBlocked('目标带有用户信息：出口不转发嵌入凭据的目标')
    host = (parsed.hostname or '').lower().strip('.')
    if not host or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise EgressBlocked('CONNECT 目标必须是 host:port 形式')
    return host, (port if port is not None else default_port), 'https'


def host_allowed(host: str, allow_hosts) -> str | None:
    """The allow list entry this host matches, or ``None``.

    An entry is either an exact host or ``*.suffix``.  A bare suffix is an
    exact host, not an implicit wildcard: allowing ``example.com`` must not
    quietly allow every subdomain somebody else runs under a shared CDN
    suffix, which is the difference between a business domain and a shared
    resource domain the plan asks to keep apart.
    """
    host = (host or '').lower().strip('.')
    for entry in allow_hosts or ():
        entry = entry.strip().lower().strip('.')
        if not entry:
            continue
        if entry.startswith('*.'):
            suffix = entry[1:]
            if host.endswith(suffix) and len(host) > len(suffix):
                return entry
        elif host == entry:
            return entry
    return None


@dataclass(frozen=True)
class EgressRules:
    """One deployment's egress rules, as both layers apply them."""
    hosts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    deny_hosts: tuple[str, ...] = ()
    ports: tuple[int, ...] = DEFAULT_PORTS
    max_request_bytes: int = 2_000_000
    max_response_bytes: int = 20_000_000
    max_total_bytes: int = 50_000_000
    allow_websockets: bool = False

    @classmethod
    def from_document(cls, document: dict) -> 'EgressRules':
        hosts = {name: tuple(document.get('hosts', {}).get(name) or ()) for name in HOST_CLASSES}
        budgets = document.get('budgets') or {}
        return cls(
            hosts=hosts,
            deny_hosts=tuple(document.get('deny_hosts') or ()),
            ports=tuple(document.get('ports') or DEFAULT_PORTS),
            max_request_bytes=int(budgets.get('request_bytes', 2_000_000)),
            max_response_bytes=int(budgets.get('response_bytes', 20_000_000)),
            max_total_bytes=int(budgets.get('total_bytes', 50_000_000)),
            allow_websockets=bool(document.get('websockets', False)))

    def document(self) -> dict:
        return {'hosts': {name: list(values) for name, values in self.hosts.items()},
                'deny_hosts': list(self.deny_hosts), 'ports': list(self.ports),
                'budgets': {'request_bytes': self.max_request_bytes,
                            'response_bytes': self.max_response_bytes,
                            'total_bytes': self.max_total_bytes},
                'websockets': self.allow_websockets}

    def destination_class(self, host: str) -> str | None:
        """Which declared class this host belongs to, checked against deny first."""
        host = (host or '').lower().strip('.')
        if host_allowed(host, self.deny_hosts) is not None:
            return None
        for name in HOST_CLASSES:
            if host_allowed(host, self.hosts.get(name, ())) is not None:
                return name
        return None

    def check_target(self, host: str, port: int) -> str:
        """The class this target is admitted under, or raise ``EgressBlocked``."""
        if port not in self.ports:
            raise EgressBlocked(f'端口 {port} 不在出口允许的端口内（{",".join(str(item) for item in self.ports)}）')
        if host_allowed(host, self.deny_hosts) is not None:
            raise EgressBlocked(f'目的主机 {host} 属于控制网络，出口一律不转发')
        name = self.destination_class(host)
        if name is None:
            raise EgressBlocked(f'目的主机 {host} 不在本次出口允许的业务域或资源域内')
        return name

    def resolve(self, host: str, port: int) -> tuple[str, ...]:
        """Every numeric address this host resolves to, all of them checked.

        Resolution happens here, once, and the caller connects to the returned
        numeric address: a name that resolves to both a public and a private
        address is refused outright rather than connected to whichever answer
        came first.  Only the first admitted address is returned for a
        connection, but admission is decided by the whole answer set.
        """
        try:
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise EgressBlocked(f'目的主机 {host} 无法解析：{error}') from None
        addresses: list[str] = []
        for entry in answers:
            address = entry[4][0]
            reason = blocked_address_reason(address)
            if reason is not None:
                raise EgressBlocked(f'目的主机 {host} 解析到 {address}（{reason}），出口不转发')
            if address not in addresses:
                addresses.append(address)
        if not addresses:
            raise EgressBlocked(f'目的主机 {host} 没有可用的地址')
        return tuple(addresses)

    def check_url(self, url: str) -> tuple[str, int, str, str]:
        """A full pre-connection check: class, port and resolved addresses.

        Returns host, port, scheme and class.  A caller that then connects to
        the hostname itself has done the resolution a second time, which is
        why the proxy connects to the numeric address this method resolved.
        """
        host, port, scheme = parse_target(url, 80 if url.startswith('http://') else 443)
        name = self.check_target(host, port)
        self.resolve(host, port)
        return host, port, scheme, name

    def endpoint(self, url: str) -> str:
        """The admitted numeric endpoint for a URL, as ``http://addr:port``.

        The address comes from the same resolution the rule check just made,
        so whatever connects next connects to what was judged — a second
        lookup by the caller could answer differently.
        """
        host, port, scheme, _name = self.check_url(url)
        address = self.resolve(host, port)[0]
        literal = f'[{address}]' if ':' in address else address
        default = 443 if scheme == 'https' else 80
        return f'{scheme}://{literal}' if port == default else f'{scheme}://{literal}:{port}'


@dataclass
class Budget:
    """One execution's transfer allowance, counted by whoever forwards bytes.

    The proxy holds the authoritative instance; a script may hold its own for
    a readable refusal, but only the proxy's count decides what leaves.  The
    limit is on bytes actually forwarded, not on a declared ``Content-Length``
    alone, so a response that lies about its length is still stopped.
    """
    limit_total: int
    limit_request: int
    limit_response: int
    used_total: int = 0
    used_request: int = 0
    used_response: int = 0

    @classmethod
    def for_rules(cls, rules: EgressRules) -> 'Budget':
        return cls(limit_total=rules.max_total_bytes, limit_request=rules.max_request_bytes,
                   limit_response=rules.max_response_bytes)

    @property
    def remaining(self) -> int:
        return max(0, self.limit_total - self.used_total)

    def charge_request(self, count: int) -> None:
        if self.used_request + count > self.limit_request:
            raise EgressBlocked(f'本次上传超过允许的 {self.limit_request} 字节')
        self._charge(count, 'request')

    def charge_response(self, count: int) -> None:
        if self.used_response + count > self.limit_response:
            raise EgressBlocked(f'本次下载超过允许的 {self.limit_response} 字节')
        self._charge(count, 'response')

    def _charge(self, count: int, side: str) -> None:
        if self.used_total + count > self.limit_total:
            raise EgressBlocked(f'本次执行的外发流量合计超过 {self.limit_total} 字节')
        self.used_total += count
        if side == 'request':
            self.used_request += count
        else:
            self.used_response += count
