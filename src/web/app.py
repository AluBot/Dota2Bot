from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI

if TYPE_CHECKING:
    from core import Dota2Bot


class MyFastAPI(FastAPI):
    def __init__(self, bot: Dota2Bot) -> None:
        super().__init__()
        self.bot: Dota2Bot = bot
