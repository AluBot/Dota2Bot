# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Healthcheck.

Pings a healthchecks.io check while the bot is healthy, so healthchecks.io alerts me when the pings stop coming.
"""

from __future__ import annotations

from typing import override

import aiohttp
from steam.ext import commands

from config import env
from core import Dota2Bot
from shared import systemd
from shared.concepts import loop, prefixed_logger

log = prefixed_logger("🩺")


class HealthCog(commands.Cog[Dota2Bot]):
    """Pings healthchecks.io while the bot is connected to Steam and the Game Coordinator and its database answers."""

    def __init__(self) -> None:
        super().__init__()
        # only the VPS (a systemd service) pings, so a bot running at home doesn't keep the check green
        if env.HEALTHCHECK_URL and systemd.is_systemd_service():
            self.healthcheck.add_exception_type(aiohttp.ClientError, TimeoutError)
            self.healthcheck.start()

    @override
    async def cog_unload(self) -> None:
        """Stop pinging."""
        self.healthcheck.cancel()

    @loop(minutes=5, wait_for_ready=True)
    async def healthcheck(self) -> None:
        """Ping the healthchecks.io check, unless Steam, the Game Coordinator or the database is down."""
        # `is_ready()` stays True while steam.py reconnects, so require a live Steam connection;
        # the Game Coordinator flag is cleared on its own disconnects.
        ws = self.bot.ws
        if ws is None or ws.socket.closed or not self.bot.is_gc_ready():
            log.warning("Healthcheck: skipping the ping, Steam or the Game Coordinator isn't connected")
            return
        try:
            await self.bot.pool.fetchval("SELECT 1")
        except Exception as exc:  # ruff: ignore[blind-except] any database failure just means "not healthy"
            log.warning("Healthcheck: skipping the ping, the database doesn't answer: {!r}", exc)
            return
        async with self.bot.session.get(env.HEALTHCHECK_URL, timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status()


async def setup(bot: Dota2Bot) -> None:
    """Load the extension (steam.py entry point)."""
    await bot.add_cog(HealthCog())
