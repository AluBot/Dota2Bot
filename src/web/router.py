from __future__ import annotations

from typing import TYPE_CHECKING, cast

import steam
from fastapi import APIRouter, HTTPException, Request, status
from steam.ext.dota2.models import User

from cogs.friends import rank_display_name
from shared import clock
from shared.types_ import local_api as schemas  # ruff: ignore[typing-only-first-party-import]

if TYPE_CHECKING:
    from core import Dota2Bot


router = APIRouter()


@router.get("/friend/{friend_id}")
async def friend(friend_id: int, request: Request) -> schemas.Friend:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    if not bot.is_gc_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="I am reconnecting to Dota 2 Game Coordinator, please wait a bit",
        )
    if not bot.friends_index:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "I have just recently restarted Dota 2 Game Coordinator, "
                "I needs some time to gather initial information, please wait a second"
            ),
        )
    friend = bot.friends_index.get(friend_id)
    if friend is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="I don't have any friends with such friend_id",
        )

    return {
        "id": friend.steam.id,
        "name": friend.steam.name,
        "is_playing_dota": friend.is_playing_dota(),
        "status": friend.rich_presence.status.display_name,
        "rich_presence": str(friend.rich_presence),
        "raw_rich_presence": friend.rich_presence.raw,
        "activity": str(friend.activity),
        "live_match": {
            "id": friend.live_match.id,
            "game_mode": friend.live_match.game_mode,
            "lobby_type": friend.live_match.lobby_type,
            "tag": friend.live_match.tag,
            "message": friend.live_match.message,
            "server_steam_id": friend.live_match.server_steam_id,
            "players": [
                {
                    "id": player.id,
                    "player_slot": player.player_slot,
                    "lifetime_games": player.lifetime_games,
                    "rank_display_name": player.rank_display_name,
                    "hero_id": player.hero.id,
                }
                for player in friend.live_match.players.values()
            ],
            "started_at": str(friend.live_match.started_at),
            "average_mmr": friend.live_match.average_mmr,
            "state": friend.live_match.state,
        }
        if friend.live_match is not None
        else None,
    }


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


@router.get("/previous_match/{friend_id}")
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


async def convert_argument_to_user(bot: Dota2Bot, argument: str) -> User:
    try:
        return await bot.fetch_user(steam.utils.parse_id64(argument))
    except steam.InvalidID:
        id64 = await steam.utils.id64_from_url(argument)
        if id64 is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Steam User Not Found",
            ) from None
        return await bot.fetch_user(id64)
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Something is wrong, steam didn't respond",
        ) from None


@router.get("/steam_user_convert/{argument:path}", name="path-convertor")
async def steam_user_convert(argument: str, request: Request) -> schemas.SteamUser:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    user = await convert_argument_to_user(bot, argument)
    return {
        "id": user.id,
        "name": user.name,
    }


@router.get(path="/accept_invite/")
async def accept_invite(friend_id: int, twitch_id: str, request: Request) -> schemas.SteamUser:
    bot: Dota2Bot = cast("Dota2Bot", request.app.bot)
    try:
        user = await bot.fetch_user(steam.utils.parse_id64(friend_id))
    except steam.InvalidID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Steam User Not Found",
        ) from None
    bot.pending_invites[user.id] = (clock.utcnow(), twitch_id)
    return {
        "id": user.id,
        "name": user.name,
    }
