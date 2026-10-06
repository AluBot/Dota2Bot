"""Dota 2 Client.

License
-------
* License: MPL-2.0, see LICENSE for more details.
* Copyright: (C) 2020-present @Aluerie.
"""

from __future__ import annotations

import logging
import platform
import sys
from typing import TYPE_CHECKING, Any, override

import steam
from steam import PersonaState
from steam.ext import dota2

from config import env
from shared.concepts.base_bot import BotBase
from shared.dota_apis.opendota import OpenDotaClient
from shared.dota_apis.steam_web_api import SteamWebAPIClient
from shared.dota_apis.stratz import StratzClient

if TYPE_CHECKING:
    from aiohttp import ClientSession

    from cogs.friends.models import PlayingMatch, SpectatingMatch, Streamer
    from shared.concepts import db

log = logging.getLogger(__name__)

__all__ = ("Dota2Bot",)


class Dota2Bot(BotBase, dota2.Bot):
    """Subclass for SteamIO's Client.

    Used to communicate with Dota 2 Game Coordinator in order to track information about my profile real-time.
    """

    def __init__(
        self,
        # *,
        session: ClientSession,
        pool: db.PoolTypedWithAny,
        # steam_web_api: str,
        # stratz_bearer: str,
    ) -> None:

        self.started: bool = False
        self.session: ClientSession = session
        self.pool: db.PoolTypedWithAny = pool

        if platform.system() == "Linux":
            # self.username is used internally by steamio
            self._username: str = env.STEAM_IRENESBOT_USERNAME
            self._password: str = env.STEAM_IRENESBOT_PASSWORD
            self.error_ping = "<@&1116171071528374394>"
        else:
            self._username: str = env.STEAM_IRENESTEST_USERNAME
            self._password: str = env.STEAM_IRENESTEST_PASSWORD
            self.error_ping = "<@&1337106675433340990>"

        super().__init__(self.session, env.WEBHOOK_ERROR, self.error_ping)
        super(dota2.Bot, self).__init__(command_prefix="!", state=PersonaState.Online)

        self.opendota = OpenDotaClient(session=session)
        self.stratz = StratzClient(bearer_token=env.STRATZ_BEARER, session=session)
        self.web_api = SteamWebAPIClient(api_key=env.STEAM_API_KEY, session=session)

        # Attributes needed for `cogs.friends`
        self.streamers: dict[int, Streamer] = {}
        self.play_matches: dict[str, PlayingMatch] = {}
        self.spectate_matches: dict[str, SpectatingMatch] = {}

    async def before_login(self) -> None:
        """Before login."""
        await self.load_extension("cogs.datafeed")
        await self.load_extension("cogs.meta")
        await self.load_extension("cogs.friends")

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
