"""Numeric public destination classification used by the current host and its egress pipe.

Extracted without changing the former gateway classification rules. This module
owns no gateway control, model routing, domain permissions or budget storage.
"""
from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

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
