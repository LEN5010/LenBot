"""Numeric public destination classification used by the current host and its egress pipe.

Extracted without changing the former gateway classification rules. This module
owns no gateway control, model routing, domain permissions or budget storage.
"""
from __future__ import annotations

import ipaddress

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

