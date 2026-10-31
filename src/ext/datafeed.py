# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Datafeed.

Keeps Dota 2 constants in the database up to date.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import NamedTuple, override

from steam.ext import commands

from core import Dota2Bot
from shared.concepts import loop

log = logging.getLogger(__name__)


class ItemToUpsert(NamedTuple):
    """A row of the ``dota_constants_items`` table."""

    item_id: int
    display_name: str


class DatafeedCog(commands.Cog[Dota2Bot]):
    """Refreshes the Dota 2 constants in the database from dota2.com's datafeed."""

    def __init__(self) -> None:
        super().__init__()
        self.refresh_dota_constants_items.start()

    @override
    async def cog_unload(self) -> None:
        """Stop the daily refresh."""
        self.refresh_dota_constants_items.cancel()

    async def upsert_constants_items(self, to_insert: list[ItemToUpsert], service_name: str) -> None:
        """Upsert data into the ``dota_constants_items`` table.

        Parameters
        ----------
        to_insert
            Items to save.
        service_name
            Where the items come from. Only used for logging.
        """
        query = """
            INSERT INTO dota_constants_items
            (item_id, display_name)
            VALUES ($1, $2)
            ON CONFLICT (item_id)
                DO UPDATE SET display_name = $2;
        """
        await self.bot.pool.executemany(query, to_insert)
        log.debug("🍋 Database Dota Constants: Updated items with %s API", service_name)

    async def _refresh_items(self) -> None:
        """Download the item list and save it into the database."""
        items = await self.bot.dota2com.item_list()
        await self.upsert_constants_items(
            to_insert=[ItemToUpsert(item_id=item.id, display_name=item.display_name) for item in items],
            service_name="dota2com's datafeed",
        )

    @loop(time=dt.time(hour=6, minute=44, tzinfo=dt.UTC), wait_for_ready=True)
    async def refresh_dota_constants_items(self) -> None:
        """Daily refresh the database's Dota constants.

        ``Dota2comClient`` already retries failed requests, so an exception that gets here is serious:
        the loop stops and the developers get notified.
        """
        log.debug("🍋 Database Dota Constants: Refreshing `dota_constants_items`")
        await self._refresh_items()

    @refresh_dota_constants_items.before_loop
    async def fill_empty_items_table(self) -> None:
        """Fill the table right away on a fresh database instead of waiting for the daily time."""
        try:
            if await self.bot.pool.fetchval("SELECT EXISTS (SELECT 1 FROM dota_constants_items);"):
                return
            log.info("🍋 Database Dota Constants: `dota_constants_items` is empty, filling it right away.")
            await self._refresh_items()
        except Exception:
            # not worth stopping the daily refresh for
            log.exception("Failed to fill the empty `dota_constants_items`")


async def setup(bot: Dota2Bot) -> None:
    """Load the extension (steam.py entry point)."""
    await bot.add_cog(DatafeedCog())
