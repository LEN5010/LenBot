"""A copyable plugin using only the public interface."""

import json

from len_bot.next.plugin import Invocation, Plugin, command, fullmatch, tool


class Counter(Plugin):
    async def show(self, ctx: Invocation) -> None:
        count = await ctx.get_kv(ctx.scene, 0)
        text = f"{ctx.config['label']}：{count}"
        if ctx.config['format'] == 'detail':
            text += f"；每次增加 {ctx.config['step']}，发送「计数加一」累加。"
        await ctx.reply(text)

    @command('计数', '查看本群计数，不调用模型')
    async def count(self, ctx: Invocation, args: str) -> None:
        await self.show(ctx)

    @fullmatch('计数加一', '按配置步长增加本群计数，不调用模型')
    async def increment(self, ctx: Invocation) -> None:
        value = await ctx.get_kv(ctx.scene, 0)
        await ctx.set_kv(ctx.scene, value + ctx.config['step'])
        await self.show(ctx)

    @command('计数清零', '清除本群计数；本示例允许群友使用')
    async def reset(self, ctx: Invocation, args: str) -> None:
        await ctx.delete_kv(ctx.scene)
        await self.show(ctx)

    @tool('counter_read', '读取当前群计数，不修改计数、不发送消息')
    async def read(self, ctx: Invocation) -> str:
        return json.dumps({'label': ctx.config['label'], 'count': await ctx.get_kv(ctx.scene, 0)},
                          ensure_ascii=False)
