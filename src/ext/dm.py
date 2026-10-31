# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""DM commands."""

from __future__ import annotations

from steam.ext import commands

from core import Dota2Bot
from utils import const


class DirectMessageCommandsCog(commands.Cog[Dota2Bot]):
    """MetaCog."""

    @commands.command(aliases=["hi", "yo"])  # ty: ignore[invalid-argument-type]
    async def hello(self, ctx: commands.Context[Dota2Bot]) -> None:
        """Say hello. The owner also gets the bot's friend list, nobody else should see it."""
        if ctx.author.id != const.IRENE_STEAM_ID32:
            await ctx.send("Hello!")
            return
        users = await self.bot.user.friends()
        await ctx.send(", ".join(str(user.id) for user in users) or "No friends yet.")


async def setup(bot: Dota2Bot) -> None:
    """Load the extension (steam.py entry point)."""
    await bot.add_cog(DirectMessageCommandsCog())
