"""Scheduled RSS 2.0 subscriptions with plugin-owned cursors and no model calls."""

import asyncio
from functools import partial
from xml.etree import ElementTree

import httpx

from len_bot.next.plugin import Invocation, Plugin, PluginContext, command


def read_feed(content: bytes) -> list[tuple[str, str]]:
    try:
        root = ElementTree.fromstring(content)
        if root.tag != "rss" or root.attrib.get("version") != "2.0":
            raise ValueError("订阅源必须是 RSS 2.0")
        channel = root.find("channel")
        if channel is None:
            raise ValueError("RSS 缺少 channel")
        items = []
        for item in channel.findall("item"):
            title, link = item.findtext("title"), item.findtext("link")
            if title is None or link is None or not title.strip() or not link.strip():
                raise ValueError(f"RSS 条目缺少 title/link：{ElementTree.tostring(item, encoding='unicode')[:300]}")
            items.append((title.strip(), link.strip()))
        return items
    except (ElementTree.ParseError, ValueError) as error:
        raise ValueError(f"RSS 解析失败：{error}；原文开头：{content[:300]!r}") from error


class RSSBroadcast(Plugin):
    def __init__(self, ctx: PluginContext):
        super().__init__(ctx)
        self.publish_locks = {(scene, subscription["name"]): asyncio.Lock()
                              for subscription in ctx.config["subscriptions"]
                              for scene in subscription["scenes"]}

    async def start(self) -> None:
        for subscription in self.ctx.config["subscriptions"]:
            for scene in subscription["scenes"]:
                self.ctx.cron(subscription["name"], subscription["cron"],
                              partial(self.publish, subscription=subscription),
                              scene=scene, timezone=subscription["timezone"])

    async def publish(self, ctx: Invocation, *, subscription: dict) -> None:
        async with self.publish_locks[ctx.scene, subscription["name"]]:
            await self.publish_serially(ctx, subscription=subscription)

    async def publish_serially(self, ctx: Invocation, *, subscription: dict) -> None:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            response = await client.get(subscription["url"])
            response.raise_for_status()
        items = read_feed(response.content)
        key = f"{ctx.scene}/{subscription['name']}"
        seen = await ctx.get_kv(key, [])
        new = [(title, link) for title, link in items if link not in seen][:subscription["limit"]]
        if not new:
            return
        text = subscription["name"] + "\n" + "\n\n".join(f"{title}\n{link}" for title, link in new)
        sent = await ctx.reply(text)
        if sent.status not in {"sent", "simulated"}:
            raise RuntimeError(sent.report)
        await ctx.set_kv(key, list(dict.fromkeys([link for _, link in new] + seen))[:100])

    @command("订阅播报", "直接播报本群订阅的新条目，可指定订阅名称，不调用模型")
    async def publish_now(self, ctx: Invocation, args: str) -> None:
        selected = [item for item in ctx.config["subscriptions"]
                    if ctx.scene in item["scenes"] and (not args or item["name"] == args)]
        if not selected:
            await ctx.reply("本群没有匹配的订阅。")
            return
        for subscription in selected:
            await self.publish(ctx, subscription=subscription)
