# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Bot."""

from __future__ import annotations

import asyncio
import logging
import platform
import sys
from typing import TYPE_CHECKING, Any, NamedTuple, override

import discord
import steam
from steam import PersonaState
from steam.ext import dota2

from config import env
from shared import fmt
from shared.apis.dota2com import Dota2comClient
from shared.apis.opendota import OpenDotaClient
from shared.apis.steam_web import SteamWebAPIClient
from shared.apis.stratz import StratzClient
from shared.concepts.base_bot import BotBase

if TYPE_CHECKING:
    import datetime as dt

    from aiohttp import ClientSession

    from ext.friends.models import Friend
    from shared.concepts import db

log = logging.getLogger(__name__)

__all__ = (
    "Dota2Bot",
    "PendingInvite",
)


class PendingInvite(NamedTuple):
    """A Steam user the bot is going to accept a friend invite from.

    Attributes
    ----------
    sent_at
        When the website has allowed the bot to accept the invite.
    twitch_id
        Twitch account that is going to be linked to the Steam account.
    """

    sent_at: dt.datetime
    twitch_id: str


class Dota2Bot(BotBase, dota2.Bot):
    """Subclass for SteamIO's Client.

    Used to communicate with Dota 2 Game Coordinator in order to track information about my profile real-time.
    """

    def __init__(
        self,
        session: ClientSession,
        pool: db.PoolTypedWithAny,
    ) -> None:
        self.started: bool = False
        self.session: ClientSession = session
        self.pool: db.PoolTypedWithAny = pool

        if platform.system() == "Linux":
            # self.username is used internally by steamio so shouldn't use it here:
            test = False
            self._username: str = env.STEAM_IRENESBOT_USERNAME
            self._password: str = env.STEAM_IRENESBOT_PASSWORD
            dev_ping = "<@&1116171071528374394>"
        else:
            test = True
            self._username: str = env.STEAM_IRENESTEST_USERNAME
            self._password: str = env.STEAM_IRENESTEST_PASSWORD
            dev_ping = "<@&1337106675433340990>"

        super().__init__(
            self.session,
            test=test,
            dev_ping=dev_ping,
            error_webhook_url=env.WEBHOOK_ERROR,
            spam_webhook_url=env.WEBHOOK_NOTIFICATION,
        )
        super(dota2.Bot, self).__init__(command_prefix="!", state=PersonaState.Online)

        self.opendota = OpenDotaClient(session=session)
        self.stratz = StratzClient(bearer_token=env.STRATZ_BEARER, session=session)
        self.steam_web = SteamWebAPIClient(api_key=env.STEAM_API_KEY, session=session)
        self.dota2com = Dota2comClient(session=session)

        # Attributes needed for `ext.friends`
        self.friends_index: dict[int, Friend] = {}  # DO NOT CONFUSE IT WITH steamIO's self.friends
        self.friend_index_ready: asyncio.Event = asyncio.Event()
        self.pending_invites: dict[int, PendingInvite] = {}

    async def before_login(self) -> None:
        """Before login."""
        await self.load_extension("ext.datafeed")
        await self.load_extension("ext.dm")
        await self.load_extension("ext.friends")

    async def _before_login(self) -> None:
        """Start helping services for steam."""
        if not self.started:
            await self.before_login()
            self.started = True

    @override
    async def login(self, *args: Any, **kwargs: Any) -> None:
        await self._before_login()
        # A potential workaround for steam login issues
        # https://github.com/Gobot1234/steam.py/issues/446
        # My service / docker files are set to restart the bot on exits
        # So it will keep restarting the bot until Steam Issues are resolved.
        try:
            await super().login(self._username, self._password)
        except steam.errors.NoCMsFound:
            log.critical("🔴 Encountered `steam.errors.NoCMsFound` - restarting. 🔴")
            sys.exit(1)
        except steam.errors.LoginError:
            log.critical("🔴 Encountered `steam.errors.LoginError` - restarting. 🔴")
            sys.exit(1)

    @override
    async def on_ready(self) -> None:
        log.info("🍋 Dota 2 Bot: Ready %s, now waiting till Game Coordinator is ready;", self.user.name)
        await self.wait_until_gc_ready()
        log.info("🍋 Dota 2 Game Coordinator: Ready")

    @override
    async def on_error(self, event: str, error: Exception, *args: Any, **kwargs: Any) -> None:
        """Event error: called when an exception is raised in an event listener.

        Parameters
        ----------
        event
            The name of the event that raised the exception.
        args
            The positional arguments for the event that raised the exception.
        kwargs
            The keyword arguments for the event that raised the exception.
        """
        embed = discord.Embed(
            color=0xA32952,
            title=f"Event Error: `{event}`",
            description=(
                f"Event Args:\n{fmt.pformat_args(*args, limit=2000)}\n"
                f"Event Kwargs:\n{fmt.pformat_mapping(kwargs, limit=1900)}"
            ),
        ).set_footer(text=f"{self.__class__.__name__}.on_error: {event}")
        await self.error_notification_manager.register(error, embed)
