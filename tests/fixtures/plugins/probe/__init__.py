"""Minimal command fixture for plugin loading and configuration boundaries."""
from len_bot.next.plugin import Plugin, command

class Probe(Plugin):
    @command("probe", "读取测试参数")
    async def report(self, ctx, args: str):
        await ctx.reply(str(ctx.config["include_detail"]))
