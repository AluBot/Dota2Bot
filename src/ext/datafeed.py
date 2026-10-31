# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Datafeed."""

from __future__ import annotations

import datetime as dt
import logging
from typing import NamedTuple, override

import aiohttp
from steam.ext import commands

from core import Dota2Bot
from shared.concepts import loop

log = logging.getLogger(__name__)


class ItemToUpsert(NamedTuple):
    item_id: int
    display_name: str


class DatafeedCog(commands.Cog[Dota2Bot]):
    """Datafeed Cog."""

    def __init__(self) -> None:
        super().__init__()
        self.refresh_dota_constants_items.start()

    @override
    async def cog_unload(self) -> None:
        self.refresh_dota_constants_items.cancel()

    async def upsert_constants_items(self, to_insert: list[ItemToUpsert], service_name: str) -> None:
        """Upsert data into `dota_constants_items` table."""
        query = """
            INSERT INTO dota_constants_items
            (item_id, display_name)
            VALUES ($1, $2)
            ON CONFLICT (item_id)
                DO UPDATE SET display_name = $2;
        """
        await self.bot.pool.executemany(query, to_insert)
        log.debug("🍋 Database Dota Constants: Updated items with %s API", service_name)

    @loop(time=dt.time(hour=6, minute=44, tzinfo=dt.UTC), wait_for_ready=True)
    async def refresh_dota_constants_items(self) -> None:
        """Daily Refresh Database's Dota Constants."""
        log.debug("🍋 Database Dota Constants: Refreshing `dota_constants_items`")
        items = await self.bot.dota2com.item_list()
        await self.upsert_constants_items(
            to_insert=[ItemToUpsert(item_id=item.id, display_name=item.display_name) for item in items],
            service_name="dota2com's datafeed",
        )

    refresh_dota_constants_items.add_exception_type(aiohttp.ClientError, TimeoutError)


async def setup(bot: Dota2Bot) -> None:
    """Load the extension (steam.py entry point)."""
    await bot.add_cog(DatafeedCog())
