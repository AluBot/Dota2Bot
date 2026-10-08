from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import steam
from fastapi import APIRouter, Request

from cogs.friends.tools import rank_medal_display_name

if TYPE_CHECKING:
    from core import Dota2Bot

router = APIRouter()


@router.get("/streamers")
async def streamers(request: Request) -> dict[int, Any]:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    return {friend_id: streamer.to_dict() for friend_id, streamer in bot.streamers.items()}


@router.get("/minimal/{match_id}")
async def minimal(match_id: int, request: Request) -> dict[str, Any]:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    match = await bot.create_partial_match(match_id).minimal()
    return {
        "id": match.id,
        "lobby_type": match.lobby_type,
        "game_mode": match.game_mode,
        "radiant_score": match.radiant_score,
        "dire_score": match.dire_score,
        "outcome": match.outcome,
        "players": [
            {
                "id": player.id,
                "hero_id": player.hero.id,
                "hero_name": player.hero.name,
                "kills": player.kills,
                "deaths": player.deaths,
                "assists": player.assists,
            }
            for player in match.players
        ],
        "start_time": match.start_time,
        "duration": match.duration.total_seconds(),
    }


@router.get("/convert/{argument:path}", name="path-convertor")
async def convert(argument: str, request: Request) -> dict[str, Any]:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)

    try:
        user = await bot.fetch_user(steam.utils.parse_id64(argument))
    except steam.InvalidID:
        id64 = await steam.utils.id64_from_url(argument)
        user = await bot.fetch_user(id64) if id64 is not None else None
    except TimeoutError:
        user = None

    return {"id": None if user is None else user.id}


@router.get("/profile_card/{friend_id}")
async def profile_card(friend_id: int, request: Request) -> dict[str, Any]:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)

    profile_card = await bot.create_partial_user(friend_id).dota2_profile_card()
    return {"medal": rank_medal_display_name(profile_card)}


@router.get("/user/{user_id}")
async def user(user_id: int, request: Request) -> dict[str, Any]:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    user = await bot.fetch_user(steam.utils.parse_id64(user_id))
    return {"name": user.name}
