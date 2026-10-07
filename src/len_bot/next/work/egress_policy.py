"""Numeric public destination classification used by the current host and its egress pipe.

Extracted without changing the former gateway classification rules. This module
owns no gateway control, model routing, domain permissions or budget storage.
"""
from __future__ import annotations

import ipaddress
import asyncio
import socket
from urllib.parse import urlsplit
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator
import httpx

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



# Real local networks a stand-in range must not cover; otherwise a domain name
# could reach them through the exemption below.
_LOCAL_NETWORKS = tuple(ipaddress.ip_network(text) for text in (
    '0.0.0.0/8', '10.0.0.0/8', '127.0.0.0/8', '169.254.0.0/16', '172.16.0.0/12', '192.168.0.0/16',
    '::1/128', 'fc00::/7', 'fe80::/10',
))


class NetworkSettings(BaseModel):
    """Host-wide facts about this machine's network, shared by host reads and task egress."""
    model_config = ConfigDict(extra='forbid', strict=True)
    # A local proxy in fake-ip mode answers every domain with an address from
    # these ranges and routes the connection by the original domain name.
    fake_ip_networks: list[str] = Field(default_factory=list)
    public_dns_url: str = 'https://dns.google/resolve'

    @field_validator('public_dns_url')
    @classmethod
    def dns_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('public_dns_url must be an HTTP(S) DNS JSON endpoint without credentials or fragment')
        return value

    @field_validator('fake_ip_networks')
    @classmethod
    def stand_in_ranges(cls, values: list[str]) -> list[str]:
        for text in values:
            network = ipaddress.ip_network(text)
            for local in _LOCAL_NETWORKS:
                if network.version == local.version and network.overlaps(local):
                    raise ValueError(f'fake_ip_networks {text} overlaps the local network {local}')
            if blocked_address_reason(str(network.network_address)) is None:
                raise ValueError(f'fake_ip_networks {text} is a public range, not a proxy stand-in range')
        return values

    def networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        return tuple(ipaddress.ip_network(text) for text in self.fake_ip_networks)


def blocked_resolved_reason(address: str, fake_ip_networks: Iterable[ipaddress.IPv4Network | ipaddress.IPv6Network]
                            ) -> str | None:
    """Like ``blocked_address_reason`` for an address that DNS returned for a domain name.

    An address inside a configured fake-ip range is the local proxy's stand-in
    for that name; the proxy connects by the name, so it is allowed.
    """
    ip = ipaddress.ip_address(address.split('%', 1)[0])
    if any(ip.version == network.version and ip in network for network in fake_ip_networks):
        return None
    return blocked_address_reason(address)


def parse_public_dns(body: dict, record_type: int) -> list[str]:
    try:
        if body['Status'] != 0:
            raise ValueError(f"DNS status {body['Status']}")
        addresses = [str(ipaddress.ip_address(answer['data'])) for answer in body.get('Answer', [])
                     if answer['type'] == record_type]
        for address in addresses:
            if ipaddress.ip_address(address).version != (4 if record_type == 1 else 6):
                raise ValueError('DNS record type differs from its numeric address family')
            reason = blocked_address_reason(address)
            if reason is not None:
                raise EgressBlocked(f'Public DNS returned {address} ({reason})')
        return addresses
    except (KeyError, TypeError, ValueError) as error:
        raise EgressBlocked(f'Invalid public DNS response: {error}; raw={repr(body)[:500]}') from error


async def public_addresses(host: str, port: int, fake_ip_networks: tuple, public_dns_url: str | None,
                           timeout_seconds: float) -> list[str]:
    """Connect to checked real addresses, never to a domain proxy's stand-in."""
    answers = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses:
        raise EgressBlocked(f'No DNS answers for {host}')
    literal = False
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        pass
    for address in addresses:
        reason = blocked_address_reason(address) if literal else blocked_resolved_reason(address, fake_ip_networks)
        if reason is not None:
            raise EgressBlocked(f'{host} resolved to {address} ({reason})')
    if not any(any(ipaddress.ip_address(address) in network for network in fake_ip_networks
                   if ipaddress.ip_address(address).version == network.version) for address in addresses):
        return addresses
    if public_dns_url is None:
        raise EgressBlocked('fake-ip public requests require network.public_dns_url')
    async with asyncio.timeout(timeout_seconds), httpx.AsyncClient(timeout=timeout_seconds, trust_env=False) as client:
        responses = await asyncio.gather(*(client.get(public_dns_url, params={'name': host, 'type': kind},
                                                      headers={'Accept': 'application/dns-json'}) for kind in ('A', 'AAAA')))
    addresses = []
    for response, kind in zip(responses, (1, 28), strict=True):
        response.raise_for_status()
        try:
            body = response.json()
        except ValueError as error:
            raise EgressBlocked(f'Invalid public DNS JSON: {response.text[:500]}') from error
        addresses.extend(parse_public_dns(body, kind))
    if not addresses:
        raise EgressBlocked(f'Public DNS returned no real addresses for {host}')
    return addresses
