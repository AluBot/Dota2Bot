from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from core import Dota2Bot

router = APIRouter()


@router.get("/")
async def read_root(request: Request):
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    return {friend_id: streamer.is_playing_dota() for friend_id, streamer in bot.streamers.items()}

    # @commands.Cog.listener()
    # async def on_user_update(self, before: dota2.User, after: dota2.User) -> None:
    #     """Event when a steam user is updated, due to one or more of their attributes changing.

    #     The information from this event is redirected to `self.bot` events
    #     so we can process it in the bot components' listeners.
    #     """
    #     """Called when bot's steam friend profile is updated.

    #     For example, this component is interested in Rich Presence changes.

    #     Note
    #     ----
    #     This is not `@commands.Component.listener("steam_user_update")` because we have to manually
    #     `.add_listener` for this function after waiting for all the clients to start.
    #     """
    #     rp_before = RichPresence(update.before.rich_presence)
    #     rp_after = RichPresence(update.after.rich_presence)

    #     if rp_before == rp_after:
    #         return

    #     friend = self.friends.setdefault(update.after.id, Friend(self.bot, update.after))
    #     friend.rich_presence = rp_after
    #     log.debug("Recognized rich presence update for %s: %s", repr(friend), repr(friend.rich_presence))
    #     await self.analyze_rich_presence(friend)
