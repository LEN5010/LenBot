"""Cron registration fixture without network or delivery work."""
from len_bot.next.plugin import Plugin

class ScheduledProbe(Plugin):
    async def start(self):
        for scene in self.ctx.scenes:
            if scene in self.ctx.config["scenes"]:
                self.ctx.cron("fixture", "0 8 * * *", self.run, scene=scene, timezone="UTC")

    async def run(self, ctx):
        return None
