"""Minimal external plugin for host configuration and permission tests."""

from len_bot.plugin import Invocation, Plugin, command


class Sample(Plugin):
    @command("示例", "回复配置中的显示选项")
    async def show(self, ctx: Invocation, args: str) -> None:
        await ctx.reply("详细" if ctx.config["show_details"] else "简洁")
