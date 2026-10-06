"""Exact time command; the mind already sees the current time in every request."""

from datetime import datetime
from zoneinfo import ZoneInfo

from len_bot.next.plugin import Invocation, Plugin, command


WEEKDAYS = "一二三四五六日"


class Clock(Plugin):
    @command("时间", "回复本场景时区的当前日期、星期和时间")
    async def now(self, ctx: Invocation, args: str) -> None:
        if args:
            await ctx.reply("用法：/时间（不带参数）")
            return
        zone = ZoneInfo(ctx.timezone())
        current = datetime.fromtimestamp(ctx.now(), zone)
        pattern = "%Y年%m月%d日 %H:%M:%S" if ctx.config["show_seconds"] else "%Y年%m月%d日 %H:%M"
        await ctx.reply(f"{current.strftime(pattern)} 星期{WEEKDAYS[current.weekday()]}（{zone.key}）")
