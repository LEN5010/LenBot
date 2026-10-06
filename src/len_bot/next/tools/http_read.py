"""Public HTTP bytes on the numeric address checked for each redirect hop."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from ..work.egress_policy import blocked_address_reason, blocked_resolved_reason


MAX_REDIRECTS = 5
ERROR_BYTES = 2048


def _target(url: str) -> tuple[str, str, int, str, str]:
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise ValueError("HTTP read URL contains whitespace or control characters")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.netloc or parts.hostname is None:
        raise ValueError(f"HTTP read requires a complete HTTP(S) URL: {url[:200]}")
    if parts.username is not None or parts.password is not None:
        raise ValueError("HTTP read URL must not contain credentials")
    host = parts.hostname
    if "%" in host:
        raise ValueError("HTTP read URL must not contain an address scope identifier")
    port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
    if port == 0:
        raise ValueError("HTTP read URL port must be 1..65535")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        ascii_host = host.encode("idna").decode("ascii")
    else:
        ascii_host = str(address)
    host_header = f"[{ascii_host}]" if ":" in ascii_host else ascii_host
    if parts.port is not None:
        host_header += f":{port}"
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))
    return clean_url, ascii_host, port, host_header, parts.scheme


async def _numeric_address(host: str, port: int, fake_ip_networks: tuple) -> str:
    try:
        address = str(ipaddress.ip_address(host))
    except ValueError:
        answers = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
        )
        if not answers:
            raise OSError(f"HTTP read DNS returned no address for {host}")
        address = answers[0][4][0]
        reason = blocked_resolved_reason(address, fake_ip_networks)
    else:
        reason = blocked_address_reason(address)
    if reason is not None:
        raise ValueError(f"HTTP read destination {address} blocked: {reason}")
    return address


async def _error_fragment(response: httpx.Response) -> str:
    fragment = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=512):
        fragment.extend(chunk[: ERROR_BYTES - len(fragment)])
        if len(fragment) == ERROR_BYTES:
            break
    return fragment.decode("utf-8", errors="replace")


async def fetch_public(url: str, timeout_seconds: float,
                       byte_limit: Callable[[str, bytes], int], *,
                       fake_ip_networks: tuple) -> tuple[str, str, bytes]:
    """GET once per hop with checked first address, preserving original Host and TLS name."""
    current = url
    for redirect in range(MAX_REDIRECTS + 1):
        current, host, port, host_header, scheme = _target(current)
        address = await _numeric_address(host, port, fake_ip_networks)
        numeric_host = f"[{address}]" if ":" in address else address
        authority = numeric_host if port == (443 if scheme == "https" else 80) else f"{numeric_host}:{port}"
        parts = urlsplit(current)
        numeric_url = urlunsplit((scheme, authority, parts.path, parts.query, ""))
        # A client belongs to exactly one hop, preventing cross-origin connection reuse.
        async with httpx.AsyncClient(
            timeout=timeout_seconds, trust_env=False, follow_redirects=False,
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False),
        ) as client:
            async with client.stream(
                "GET", numeric_url, headers={"Host": host_header},
                extensions={"sni_hostname": host},
            ) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if location is None or not location.strip():
                        fragment = await _error_fragment(response)
                        raise ValueError(f"HTTP read status {response.status_code} redirect has no Location; "
                                         f"response fragment: {fragment}")
                    if redirect == MAX_REDIRECTS:
                        fragment = await _error_fragment(response)
                        raise ValueError(f"HTTP read status {response.status_code}: exceeded {MAX_REDIRECTS} redirects; "
                                         f"response fragment: {fragment}")
                    current = urljoin(current, location)
                    continue
                if not response.is_success:
                    fragment = await _error_fragment(response)
                    raise ValueError(f"HTTP read status {response.status_code}: {fragment}")
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                body = bytearray()
                prefix = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(prefix) < 5:
                        prefix.extend(chunk[: 5 - len(prefix)])
                    limit = byte_limit(media_type, bytes(prefix))
                    if len(body) + len(chunk) > limit:
                        raise ValueError(f"HTTP read resource exceeds {limit} decoded bytes")
                    body.extend(chunk)
                return current, response.headers.get("content-type", ""), bytes(body)
    raise AssertionError("redirect loop exhausted without returning")
