from __future__ import annotations

from steam.ext import commands

from core import Dota2Bot


class MetaCog(commands.Cog[Dota2Bot]):
    """MetaCog."""

    @commands.command(aliases=["hi", "yo"])
    async def hello(self, ctx: commands.Context) -> None:
        """Hello."""
        await ctx.send("hello")


async def setup(bot: Dota2Bot) -> None:
    """Load Dota2Bot's extension. Framework of steamio."""
    await bot.add_cog(MetaCog())
