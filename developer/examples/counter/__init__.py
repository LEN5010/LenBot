"""A copyable plugin using only the public interface."""

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

    @tool('counter_read', '读取当前群计数，不修改计数、不发送消息', summary='查询本群当前计数')
    async def read(self, ctx: Invocation) -> dict:
        return {'label': ctx.config['label'], 'count': await ctx.get_kv(ctx.scene, 0)}

    @tool('counter_card', '在后台用模型生成本群计数的简短说明，完成后插件自行发到本群。'
          '调用只表示开始，不能声称已经发送；不修改计数。', summary='后台生成并发送本群计数说明')
    async def card(self, ctx: Invocation) -> dict:
        count = await ctx.get_kv(ctx.scene, 0)
        self.ctx.start_task('counter-card', self._card(ctx.scene, count))
        return {'status': 'started', 'delivery': 'plugin', 'count': count}

    async def _card(self, scene: str, count: int) -> None:
        text = await self.ctx.generate(scene, f"用一句话说明本群计数为 {count}。")
        sent = await self.ctx.send(scene, text)
        await self.ctx.emit_event(scene, f"计数说明发送结果：{sent.status}；{sent.report}")
