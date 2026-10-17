from __future__ import annotations

from typing import TYPE_CHECKING, cast

import steam
from fastapi import APIRouter, HTTPException, Request, status

from cogs.friends import rank_display_name
from shared.types_ import local_api as schemas

if TYPE_CHECKING:
    from core import Dota2Bot


router = APIRouter()


@router.get("/minimal/{match_id}")
async def minimal(match_id: int, request: Request) -> schemas.MinimalMatch:
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
                "kills": player.kills,
                "deaths": player.deaths,
                "assists": player.assists,
            }
            for player in match.players
        ],
        "start_time": str(match.start_time),
        "duration": int(match.duration.total_seconds()),
    }


@router.get("/previous_match/{friend_id}", response_model=schemas.PreviousMatch)
async def previous_match(friend_id: int, request: Request) -> schemas.PreviousMatch:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    match_history = await bot.create_partial_user(friend_id).match_history()

    match = next(iter(match_history), None)
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="It seems I can't see this account's match history yet",
        )
    return {
        "id": match.id,
        "hero_id": match.hero.id,
        "win": match.win,
        "abandon": match.abandon,
        "game_mode": match.game_mode,
        "lobby_type": match.lobby_type,
        "start_time": str(match.start_time),
        "duration": int(match.duration.total_seconds()),
    }


@router.get("/profile_card/{friend_id}")
async def profile_card(friend_id: int, request: Request) -> schemas.ProfileCard:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)

    profile_card = await bot.create_partial_user(friend_id).dota2_profile_card()
    return {
        "rank_display_name": rank_display_name(profile_card),
    }


@router.get("/steam_user/{friend_id}")
async def steam_user(friend_id: int, request: Request) -> schemas.SteamUser:
    """Get Steam User Information by their friend_id."""
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    try:
        id64 = steam.utils.parse_id64(friend_id)
    except steam.InvalidID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID",
        ) from None
    else:
        user = await bot.fetch_user(id64)
        return {
            "id": user.id,
            "name": user.name,
        }


@router.get("/steam_user_convert/{argument:path}", name="path-convertor")
async def steam_user_convert(argument: str, request: Request) -> schemas.SteamUser:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)

    try:
        user = await bot.fetch_user(id=steam.utils.parse_id64(argument))
    except steam.InvalidID:
        id64 = await steam.utils.id64_from_url(argument)
        if id64 is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Steam User Not Found",
            ) from None
        user = await bot.fetch_user(id64)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Something is wrong, steam didn't respond",
        ) from None

    return {
        "id": user.id,
        "name": user.name,
    }


@router.get("/streamer/{friend_id}")
async def streamer(friend_id: int, request: Request) -> schemas.Streamer:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    if not bot.is_gc_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="I am reconnecting to Dota 2 Game Coordinator, please wait a bit",
        )
    if not bot.streamers:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "I have just recently restarted Dota 2 Game Coordinator, "
                "I needs some time to gather initial information, please wait a second"
            ),
        )
    streamer = bot.streamers.get(friend_id)
    if streamer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="I don't have any friends with such friend_id",
        )

    return {
        "id": streamer.steam.id,
        "name": streamer.steam.name,
        "is_playing_dota": streamer.is_playing_dota(),
        "status": streamer.rich_presence.status.display_name,
        "rich_presence": str(streamer.rich_presence),
        "raw_rich_presence": streamer.rich_presence.raw,
        "activity": str(streamer.activity),
        "live_match": {
            "id": streamer.live_match.id,
            "game_mode": streamer.live_match.game_mode,
            "lobby_type": streamer.live_match.lobby_type,
            "tag": streamer.live_match.tag,
            "message": streamer.live_match.message,
            "server_steam_id": streamer.live_match.server_steam_id,
            "players": [
                {
                    "id": player.id,
                    "player_slot": player.player_slot,
                    "lifetime_games": player.lifetime_games,
                    "rank_display_name": player.rank_display_name,
                    "hero_id": player.hero.id,
                }
                for player in streamer.live_match.players.values()
            ],
            "started_at": str(streamer.live_match.started_at),
            "average_mmr": streamer.live_match.average_mmr,
            "state": streamer.live_match.state,
        }
        if streamer.live_match is not None
        else None,
    }
