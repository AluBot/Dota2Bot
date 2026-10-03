"""Dota 2 Client.

License
-------
* License: MPL-2.0, see LICENSE for more details.
* Copyright: (C) 2020-present @Aluerie.
"""

from __future__ import annotations

import logging
import platform
from typing import TYPE_CHECKING, Any, override

from steam import PersonaState
from steam.ext import dota2

from config import env

from .opendota import OpenDotaClient
from .steam_web_api import SteamWebAPIClient
from .stratz import StratzClient

if TYPE_CHECKING:
    from aiohttp import ClientSession

    from shared.concepts import db

log = logging.getLogger(__name__)

__all__ = ("Dota2Bot",)


class Dota2Bot(dota2.Bot):
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
        super().__init__(command_prefix="!", state=PersonaState.Online)
        self.started: bool = False
        self.session: ClientSession = session
        self.pool: db.PoolTypedWithAny = pool

        self.opendota = OpenDotaClient(session=session)
        self.stratz = StratzClient(bearer_token=env.STRATZ_BEARER, session=session)
        self.web_api = SteamWebAPIClient(api_key=env.STEAM_API_KEY, session=session)

    async def before_login(self) -> None:
        """Before login."""
        await self.load_extension("cogs.datafeed")
        await self.load_extension("cogs.meta")

    async def _before_login(self) -> None:
        """Start helping services for steam."""
        if not self.started:
            await self.before_login()
            self.started = True

    @override
    async def login(self, *args: Any, **kwargs: Any) -> None:
        await self._before_login()
        if platform.system() == "Linux":
            username, password = env.STEAM_IRENESBOT_USERNAME, env.STEAM_IRENESBOT_PASSWORD
        else:
            username, password = env.STEAM_IRENESTEST_USERNAME, env.STEAM_IRENESTEST_PASSWORD
        await super().login(username, password)

    @override
    async def on_ready(self) -> None:
        log.info("🍋 Dota 2 Bot: Ready %s, now waiting till Game Coordinator is ready;", self.user.name)
        await self.wait_until_gc_ready()
        log.info("🍋 Dota 2 Game Coordinator: Ready")
