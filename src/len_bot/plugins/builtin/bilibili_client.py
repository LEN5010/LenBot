"""Shared, deliberately small public Bilibili HTTP client for built-in plugins."""
from __future__ import annotations
from typing import Any
from len_bot.plugins.net_policy import validate_url

async def public_json(client, url: str, params: dict[str, Any]) -> dict:
    allowed, reason = validate_url(url)
    if not allowed:
        raise ValueError(f"安全拦截: {reason}")
    response = await client.get(url, params=params)
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict) or body.get("code") != 0:
        raise ValueError(f"B站接口返回错误: {(body or {}).get('message', 'unknown')}")
    return body

async def video_view(client, *, bvid: str | None = None, aid: int | None = None) -> dict:
    params = {"bvid": bvid} if bvid else {"aid": aid}
    return (await public_json(client, "https://api.bilibili.com/x/web-interface/view", params))["data"]

async def first_play_url(client, resource_id: str, cid: int) -> str | None:
    body = await public_json(client, "https://api.bilibili.com/x/player/playurl", {"bvid": resource_id, "cid": cid, "fnval": 16})
    durl = ((body.get("data") or {}).get("durl") or [{}])[0].get("url")
    return durl if isinstance(durl, str) and durl.startswith("https://") else None
