"""Use single generation for summaries and the existing worker for file requests."""

from importlib.resources import files
import json

from len_bot.next.plugin import Invocation, Plugin, command


def recent(ctx: Invocation) -> str:
    return json.dumps([
        {"sender": message.sender.uid, "nickname": message.sender.card or message.sender.nickname,
         "time": message.time, "is_self": message.is_self, "send_status": message.send_status,
         "text": "".join(segment.data["text"] if segment.type == "text" else f"[{segment.type}]"
                         for segment in message.segments)}
        for message in ctx.recent_messages(ctx.config["message_limit"])
        if ctx.message is None or message.id != ctx.message.id
    ], ensure_ascii=False)


class GroupDigest(Plugin):
    @command("群总结", "对本群最近消息做一次显式模型生成，可补充总结要求")
    async def summarize(self, ctx: Invocation, args: str) -> None:
        system = (files("len_bot") / "prompts/plugin_group_digest.md").read_text(encoding="utf-8")
        result = await ctx.generate(json.dumps({"要求": args or "概括最近的话题与未完事项", "群聊记录": recent(ctx)},
                                               ensure_ascii=False),
                                    role=ctx.config["model_role"], system=system)
        await ctx.reply(result)

    @command("群工作", "基于最近群聊委派长工作或文件交付；参数是实际交付要求")
    async def work(self, ctx: Invocation, args: str) -> None:
        if not args:
            await ctx.reply("用法：/群工作 交付要求，例如把刚才讨论的活动安排做成 CSV 文件。")
            return
        task = await ctx.delegate(args, args, context=recent(ctx))
        await ctx.reply(f"已接下这项工作（任务 {task['id']}），完成后再告诉你。")
