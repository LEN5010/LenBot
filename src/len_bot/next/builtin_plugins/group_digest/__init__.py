"""Group summaries: a short one on request, and a daily card built in batches from a time window."""

from importlib.resources import files
import json
import re
from typing import Annotated

from pydantic import Field

from len_bot.next.plugin import Invocation, Plugin, command, tool

from . import card, daily

MIN_MESSAGES = 5


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
    async def start(self) -> None:
        self.running: set[str] = set()
        match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", str(self.ctx.config["daily_time"]))
        if match is None:
            raise ValueError(f"每天几点发要写成 22:00 这样的 24 小时制时间：{self.ctx.config['daily_time']!r}")
        hour, minute = int(match[1]), int(match[2])
        for scene in self.ctx.config["daily_scenes"]:
            if scene in self.ctx.enabled_scenes:
                self.ctx.cron("daily-summary", f"{minute} {hour} * * *", self.scheduled,
                              scene=scene, timezone=self.ctx.timezone(scene))

    async def scheduled(self, ctx: Invocation) -> None:
        if ctx.scene not in self.running:
            await self.produce(ctx.scene, 24, requested=False)

    def begin(self, scene: str, hours: int) -> str:
        """Start in the background; the caller (a chat turn or a command) does not wait for the model."""
        if scene in self.running:
            return "这个群的总结已经在生成了，完成后会发出来。"
        self.running.add(scene)
        self.ctx.start_task(f"群聊总结 {scene}", self.produce(scene, hours, requested=True))
        return f"开始生成过去 {hours} 小时的群聊总结，生成好后卡片会直接发到群里。"

    async def produce(self, scene: str, hours: int, *, requested: bool) -> None:
        self.running.add(scene)
        try:
            before = self.ctx.now()
            after = before - hours * 3600
            lines = daily.read_window(self.ctx, scene, after, before)
            if len(lines) < MIN_MESSAGES:
                if requested:
                    await self.ctx.send(scene, f"过去 {hours} 小时群里只有 {len(lines)} 条消息，这次就不做总结了。")
                return
            summary = await daily.summarize(self.ctx, scene, lines, after, before,
                                            role=self.ctx.config["model_role"], budget=self.ctx.config["batch_chars"])
            image = card.render(summary, card.find_font(self.ctx.config["font_path"]),
                                "今日群聊总结" if hours == 24 else f"最近 {hours} 小时群聊总结")
            await self.ctx.send_image(scene, image, summary.description())
        except Exception as error:
            self.ctx.report_error(f"群聊总结 {scene}", error)
            if requested:
                await self.ctx.send(scene, f"群聊总结没生成出来：{error}")
        finally:
            self.running.discard(scene)

    @command("群总结", "对本群最近消息做一次显式模型生成，可补充总结要求")
    async def summarize(self, ctx: Invocation, args: str) -> None:
        system = (files("len_bot") / "prompts/plugin_group_digest.md").read_text(encoding="utf-8")
        result = await ctx.generate(json.dumps({"要求": args or "概括最近的话题与未完事项", "群聊记录": recent(ctx)},
                                               ensure_ascii=False),
                                    role=ctx.config["model_role"], system=system)
        await ctx.reply(result)

    @command("群日报", "生成本群过去 24 小时的总结卡片；可跟小时数，例如 /群日报 6")
    async def report(self, ctx: Invocation, args: str) -> None:
        text = args.strip()
        if text and not (text.isdigit() and 1 <= int(text) <= 72):
            await ctx.reply("用法：/群日报 或 /群日报 小时数（1–72）")
            return
        await ctx.reply(self.begin(ctx.scene, int(text) if text else 24))

    @tool("group_summary_card", "生成本群过去一段时间的群聊总结卡片并发到群里，用于有人想看今天或最近群里聊了什么。"
                                "在后台生成，调用后立即返回；卡片做好后会自动发出，不需要再转述内容。")
    async def summary_tool(self, ctx: Invocation, hours: Annotated[int, Field(ge=1, le=72)] = 24) -> str:
        return self.begin(ctx.scene, hours)

    @command("群工作", "基于最近群聊委派长工作或文件交付；参数是实际交付要求")
    async def work(self, ctx: Invocation, args: str) -> None:
        if not args:
            await ctx.reply("用法：/群工作 交付要求，例如把刚才讨论的活动安排做成 CSV 文件。")
            return
        task = await ctx.delegate(args, args, context=recent(ctx))
        await ctx.reply(f"已接下这项工作（任务 {task['id']}），完成后再告诉你。")
