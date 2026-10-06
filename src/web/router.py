from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import steam
from fastapi import APIRouter, Request

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
        "radiant_score": match.radiant_score,
        "dire_score": match.dire_score,
        "lobby_type": match.lobby_type,
        "game_mode": match.game_mode,
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


@router.get("/user/{argument}")
async def user(argument: str, request: Request) -> dict[str, Any]:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)

    try:
        user = await bot.fetch_user(steam.utils.parse_id64(argument))
    except steam.InvalidID:
        id64 = await steam.utils.id64_from_url(argument)
        if id64 is None:
            user = None
        user = await bot.fetch_user(id64)
    except TimeoutError:
        user = None

    return {"id": None if user is None else user.id}
