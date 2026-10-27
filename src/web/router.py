"""Router.

Notices
-------
* MPL-2.0 License, see LICENSE file for more details.
* Copyright (C) 2020-present @Aluerie.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Annotated, cast

import steam
from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ext.friends import rank_display_name
from shared import clock
from shared.typings import local as schemas  # ruff: ignore[typing-only-first-party-import]
from utils import const

if TYPE_CHECKING:
    from collections.abc import Generator

    from steam.ext.dota2.models import User

    from core import Dota2Bot


router = APIRouter()


########################################################################################################################
# HELPERS
########################################################################################################################


def get_bot(request: Request) -> Dota2Bot:
    """Get the bot that runs this API."""
    return cast("Dota2Bot", request.app.bot)


def require_gc_ready(bot: Dota2Bot) -> None:
    """Raise 503 if the bot is not connected to Dota 2 Game Coordinator, which most endpoints depend on."""
    if not bot.is_gc_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="I am reconnecting to Dota 2 Game Coordinator, please wait a bit",
        )


@contextlib.contextmanager
def gc_timeout() -> Generator[None]:
    """Turn Dota 2 Game Coordinator not responding into an HTTP error."""
    try:
        yield
    except TimeoutError:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Dota 2 Game Coordinator didn't respond in time, please try again",
        ) from None


def steam_timeout() -> HTTPException:
    """Get the error for Steam not responding."""
    return HTTPException(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        detail="Something is wrong, steam didn't respond",
    )


async def fetch_user_by_friend_id(bot: Dota2Bot, friend_id: int) -> User:
    """Fetch a Steam user by their friend id (account id), turning Steam failures into HTTP errors."""
    try:
        id64 = steam.utils.parse_id64(friend_id)
    except steam.InvalidID:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid ID",
        ) from None
    try:
        return await bot.fetch_user(id64)
    except TimeoutError:
        raise steam_timeout() from None


async def convert_argument_to_user(bot: Dota2Bot, argument: str) -> User:
    """Fetch a Steam user by anything that identifies them: any kind of Steam ID or a profile url."""
    try:
        try:
            id64 = steam.utils.parse_id64(argument)
        except steam.InvalidID:
            id64 = await steam.utils.id64_from_url(argument)
            if id64 is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Steam User Not Found",
                ) from None
        return await bot.fetch_user(id64)
    except TimeoutError:
        raise steam_timeout() from None


########################################################################################################################
# ENDPOINTS
########################################################################################################################


@router.get("/friend/{friend_id}")
async def friend(
    friend_id: Annotated[
        int,
        Path(
            description="Steam friend id (account id)",
            openapi_examples={
                "streamer": {"summary": "A streamer in the friend list", "value": const.IRENE_STEAM_ID32},
                "unknown": {"summary": "Not a friend, should 404", "value": 1},
            },
        ),
    ],
    request: Request,
) -> schemas.Friend:
    """Get the current state of a streamer from the bot's friend list."""
    bot = get_bot(request)
    require_gc_ready(bot)
    if not bot.friend_index_ready.is_set():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "I have just recently restarted Dota 2 Game Coordinator, "
                "I need some time to gather initial information, please wait a second"
            ),
        )
    streamer = bot.friends_index.get(friend_id)
    if streamer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="I don't have any friends with such friend_id",
        )

    live = streamer.live_match
    return {
        "id": streamer.steam.id,
        "name": streamer.steam.name,
        "is_playing_dota": streamer.is_playing_dota(),
        "status": streamer.rich_presence.status.display_name,
        "party": streamer.rich_presence.party_ids,
        "rich_presence": str(streamer.rich_presence),
        "raw_rich_presence": streamer.rich_presence.raw,
        "live_match": {
            "id": live.id,
            "game_mode": live.game_mode,
            "lobby_type": live.lobby_type,
            "tag": live.tag,
            "message": live.message,
            "server_steam_id": live.server_steam_id,
            "players": [
                {
                    "id": player.id,
                    "player_slot": player.player_slot,
                    "lifetime_games": player.lifetime_games,
                    "rank_display_name": player.rank_display_name,
                    "hero_id": player.hero.id,
                }
                for player in live.players.values()
            ],
            "started_at": str(live.started_at),
            "average_mmr": live.average_mmr,
            "state": live.state,
        }
        if live is not None
        else None,
    }


@router.get("/minimal/{match_id}")
async def minimal(match_id: int, request: Request) -> schemas.MinimalMatch:
    """Get minimal information about a finished match."""
    bot = get_bot(request)
    require_gc_ready(bot)
    with gc_timeout():
        try:
            match = await bot.create_partial_match(match_id).minimal()
        except ValueError:
            # e.g. practice lobbies leave no trace in the match history
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="I can't find such a match in the match history",
            ) from None
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
async def previous_match(
    friend_id: int,
    request: Request,
    index: Annotated[int, Query(ge=0, lt=20, description="How far to look back: 0 is the latest match")] = 0,
) -> schemas.PreviousMatch:
    """Get a match from the account's match history."""
    bot = get_bot(request)
    require_gc_ready(bot)
    with gc_timeout():
        matches = list(await bot.create_partial_user(friend_id).match_history())
    if index >= len(matches):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="It seems I can't see this match in the account's match history",
        )
    match = matches[index]
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
    """Get the account's Dota 2 rank medal."""
    bot = get_bot(request)
    require_gc_ready(bot)
    with gc_timeout():
        card = await bot.create_partial_user(friend_id).dota2_profile_card()
    return {
        "rank_display_name": rank_display_name(card),
    }


@router.get("/steam_user/{friend_id}")
async def steam_user(friend_id: int, request: Request) -> schemas.SteamUser:
    """Get Steam User Information by their friend_id."""
    user = await fetch_user_by_friend_id(get_bot(request), friend_id)
    return {
        "id": user.id,
        "name": user.name,
    }


@router.get("/steam_user_convert/{argument:path}", name="path-convertor")
async def steam_user_convert(argument: str, request: Request) -> schemas.SteamUser:
    """Get Steam User Information by anything that identifies them: any kind of Steam ID or a profile url."""
    user = await convert_argument_to_user(get_bot(request), argument)
    return {
        "id": user.id,
        "name": user.name,
    }


@router.get(path="/accept_invite/")
async def accept_invite(friend_id: int, twitch_id: str, request: Request) -> schemas.SteamUser:
    """Allow the bot to accept a friend invite from this Steam user for the next few minutes."""
    bot = get_bot(request)
    user = await fetch_user_by_friend_id(bot, friend_id)
    bot.pending_invites[user.id] = (clock.utcnow(), twitch_id)
    return {
        "id": user.id,
        "name": user.name,
    }
