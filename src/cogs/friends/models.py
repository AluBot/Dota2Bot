from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from steam.ext import dota2

    from core import Dota2Bot


class Streamer:
    def __init__(self, bot: Dota2Bot, steam: dota2.User) -> None:
        self._bot: Dota2Bot = bot
        self.steam: dota2.User = steam

    def is_playing_dota(self) -> bool:
        """Whether this friend is playing dota or not."""
        return bool(app := self.steam.app) and app.id == 570
