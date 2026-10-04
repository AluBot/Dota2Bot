from __future__ import annotations

from steam.ext import commands

from core import Dota2Bot


class MetaCog(commands.Cog[Dota2Bot]):
    """MetaCog."""

    @commands.command(aliases=["hi", "yo"])  # ty: ignore[invalid-argument-type]
    async def hello(self, ctx: commands.Context[Dota2Bot]) -> None:
        """Hello."""
        users = await self.bot.user.friends()
        result = [user.id for user in users]
        await ctx.send(result)


async def setup(bot: Dota2Bot) -> None:
    """Load Dota2Bot's extension. Framework of steamio."""
    await bot.add_cog(MetaCog())
