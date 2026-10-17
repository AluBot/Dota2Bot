from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from shared.globs import TwitchEmote

if TYPE_CHECKING:
    from core import Dota2Bot


class MyFastAPI(FastAPI):
    def __init__(self, bot: Dota2Bot) -> None:
        super().__init__(
            exception_handlers={
                Exception: exception_handler,
            },
        )
        self.bot: Dota2Bot = bot


async def exception_handler(request: Request, exception: Exception) -> JSONResponse:
    """Exception Handler."""
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    embed = discord.Embed(title="Router Error")
    await bot.error_notification_manager.register(exception, embed)
    return JSONResponse(
        status_code=status.HTTP_418_IM_A_TEAPOT,
        content={
            "detail": f"Oups, problem with Dota 2 Bot {TwitchEmote.CaitThinking} Sent Irene notification about it",
        },
    )
