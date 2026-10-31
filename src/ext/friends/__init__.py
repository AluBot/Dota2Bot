# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Friends.

Follows the bot's Steam friends through Dota 2 Rich Presence and inspects their live matches.

The extension is split into

* ``presence`` - parsing Rich Presence into statuses and activities;
* ``models`` - friends and players;
* ``matches`` - collecting the data of live matches;
* ``queries`` - all the SQL;
* ``cog`` - the component which glues everything together.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .cog import FriendsCog

if TYPE_CHECKING:
    from core import Dota2Bot


async def setup(bot: Dota2Bot) -> None:
    """Load the extension (steam.py entry point).

    Parameters
    ----------
    bot
        The bot to add the cog to.
    """
    await bot.add_cog(FriendsCog())
