from __future__ import annotations

import asyncio
import logging
from typing import override

from steam.ext import commands

from core import Dota2Bot
from shared.concepts import tasks

from .models import Streamer

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class FriendsCog(commands.Cog[Dota2Bot]):
    """FriendsCog."""

    def __init__(self) -> None:
        super().__init__()

        self.fill_streamers.start()
        self.streamer_index_ready: asyncio.Event = asyncio.Event()

    @override
    async def cog_unload(self) -> None:
        """Cog Unload."""
        self.fill_streamers.cancel()

    @tasks.loop(count=1)
    async def fill_streamers(self) -> None:
        """Index bot's friend list on bot's startup.

        Also makes initial analyse of their rich presences.
        """
        log.debug("Indexing bot's friend list.")
        for friend in await self.bot.user.friends():
            self.bot.streamers[friend.id] = Streamer(self.bot, friend)  # _user ???

        # for friend in self.friends.values():
        #     await self.analyze_rich_presence(friend)

        log.debug('Friends index ready. Setting "_friends_index_ready".')
        self.streamer_index_ready.set()

    @fill_streamers.before_loop
    async def wait_for_clients(self) -> None:
        """Wait for all the needed clients to get ready.

        Otherwise, such things as Dota 2 Coordinator requests are going to error out.
        Unfortunately, specifically, Dota 2 Coordinator usually takes ~30 seconds to get ready.
        """
        await self.bot.wait_until_ready()
        # await self.bot.wait_until_gc_ready()
