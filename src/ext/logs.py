# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Logs mirror.

Copies the bot's log records into the logs channel through the `WEBHOOK_LOGGER` Discord webhook.
"""

from __future__ import annotations

from typing import Final, override

from steam.ext import commands

from config import env
from core import Dota2Bot
from shared.concepts.discord_logs import DiscordWebhookLogs


class LogsViaWebhookCog(DiscordWebhookLogs, commands.Cog[Dota2Bot]):
    """Mirrors log records (INFO and above) to the `WEBHOOK_LOGGER` Discord webhook."""

    EXTRA_EXACT_AVATAR_MAPPING: Final[dict[str, str]] = {
        "core.bot": "https://em-content.zobj.net/source/microsoft/476/crossed-swords_2694-fe0f.png",
    }

    def __init__(self, bot: Dota2Bot) -> None:
        super().__init__(env.WEBHOOK_LOGGER, bot.session)
        super(DiscordWebhookLogs, self).__init__()

    @override
    async def cog_unload(self) -> None:
        """Stop mirroring logs."""
        await self.teardown()


async def setup(bot: Dota2Bot) -> None:
    """Load the extension (steam.py entry point).

    steam.py cogs have no `cog_load`, so the mirror starts here.
    """
    cog = LogsViaWebhookCog(bot)
    await cog.load()
    await bot.add_cog(cog)
