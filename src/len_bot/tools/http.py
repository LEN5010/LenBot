"""Bounded public HTTP reads; validate every redirect, including relative ones."""
from urllib.parse import urljoin
import httpx
from len_bot.plugins.net_policy import validate_url


class PublicReadError(ValueError):
    pass


async def fetch_public(client: httpx.AsyncClient, url: str, *, max_bytes=2_000_000):
    for _ in range(6):
        allowed, reason = validate_url(url)
        if not allowed:
            raise PublicReadError(f"安全拦截: {reason}")
        async with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise PublicReadError("Redirect has no Location")
                url = urljoin(str(response.url), location)
                continue
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise PublicReadError("Resource exceeds download size limit")
                chunks.append(chunk)
            return str(response.url), response.headers, b"".join(chunks)
    raise PublicReadError("Redirect limit exceeded")
