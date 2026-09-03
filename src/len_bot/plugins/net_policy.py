"""SSRF Network Policy Guard (ADR-0030, §21.4).

Validates target hostnames and IPs against private, loopback, link-local,
and reserved internal ranges before outbound HTTP requests are made by agent tools.
"""

import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

BLOCKED_DOMAINS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}

BLOCKED_SUFFIXES = (
    ".local",
    ".internal",
    ".lan",
    ".home.arpa",
)


def is_ip_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    for net in BLOCKED_NETWORKS:
        if ip in net:
            return True
    return False


def validate_url(url: str) -> tuple[bool, str]:
    """Validates URL against SSRF policy.
    Returns (True, "") if allowed, or (False, reason) if blocked.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL format: {e}"

    if parsed.scheme not in ("http", "https"):
        return False, f"Unsupported scheme '{parsed.scheme}': only http and https are allowed."

    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "Empty or missing hostname in URL."

    if host in BLOCKED_DOMAINS or any(host.endswith(sfx) for sfx in BLOCKED_SUFFIXES):
        return False, f"Access to private/internal domain '{host}' is forbidden."

    # Check if host is an IP literal
    try:
        ip = ipaddress.ip_address(host)
        if is_ip_blocked(ip):
            return False, f"Access to private/loopback/reserved IP address '{ip}' is forbidden."
        return True, ""
    except ValueError:
        pass

    # Resolve hostname to IP to prevent DNS rebinding / private resolution
    try:
        addr_info = socket.getaddrinfo(host, None)
        for entry in addr_info:
            sockaddr = entry[4]
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if is_ip_blocked(ip):
                return False, f"Domain '{host}' resolved to restricted IP '{ip_str}'."
    except socket.gaierror:
        # DNS resolution error; let caller handle it
        pass

    return True, ""
