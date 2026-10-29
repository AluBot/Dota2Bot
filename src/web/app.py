from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from shared import clock
from shared.globs import TwitchEmote

if TYPE_CHECKING:
    from core import Dota2Bot

log = logging.getLogger(__name__)


class MyFastAPI(FastAPI):
    """FastAPI application bound to the Discord bot.

    Attributes
    ----------
    bot
        Bot instance used to send error notifications.
    """

    def __init__(self, bot: Dota2Bot) -> None:
        self.bot: Dota2Bot = bot
        super().__init__(exception_handlers={Exception: self._handle_unhandled_exception})

    async def _handle_unhandled_exception(self, request: Request, exception: Exception) -> JSONResponse:
        """Notify about unhandled exceptions and return a generic error response.

        Parameters
        ----------
        request
            Request during which the exception was raised.
        exception
            Unhandled exception.

        Returns
        -------
        JSONResponse
            Generic error response for the client.
        """
        route = request.scope.get("route")
        embed = (
            discord
            .Embed(
                title="Router Error",
                colour=discord.Colour.red(),
                timestamp=clock.utcnow(),
            )
            .add_field(name="Endpoint", value=f"`{request.method} {request.url.path}`", inline=False)
            .add_field(name="Route", value=f"`{getattr(route, 'name', 'unknown')}`")
            .add_field(name="Client", value=f"`{request.client.host if request.client else 'unknown'}`")
            .add_field(name="Query", value=f"`{request.url.query or '-'}`"[:1024], inline=False)
            .add_field(name="Exception", value=f"`{type(exception).__name__}`: {exception}"[:1024], inline=False)
        )

        try:
            await self.bot.error_notification_manager.register(exception, embed)
        except Exception:
            log.exception("Failed to register error notification")

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": (
                    f"Oops, problem with Dota 2 Bot {TwitchEmote.CaitThinking} Sent Irene a notification about it"
                ),
            },
        )
