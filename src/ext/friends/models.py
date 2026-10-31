# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Models.

Things the bot tracks: the friends from its Steam friend list and the players of their matches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Self, override

from .presence import RichPresence

if TYPE_CHECKING:
    from steam.ext import dota2
    from steam.ext.dota2 import Hero

    from core import Dota2Bot

    from .matches import LiveMatch

__all__ = (
    "Friend",
    "Player",
    "rank_display_name",
)

DOTA_APP_ID = 570


def rank_display_name(profile_card: dota2.ProfileCard) -> str:
    """Get a human-readable rank medal string out of a player's Dota 2 Profile Card.

    Parameters
    ----------
    profile_card
        Dota 2 Profile Card of the player.
    """
    display_name = profile_card.rank_tier.division
    if stars := profile_card.rank_tier.stars:
        display_name += f" \N{BLACK STAR}{stars}"
    if number_rank := profile_card.leaderboard_rank:
        display_name += f" #{number_rank}"
    return display_name


class Friend:
    """A friend from the bot's Steam friend list.

    Attributes
    ----------
    steam
        The Steam user, refreshed on every ``on_user_update`` event.
    rich_presence
        The last parsed Dota 2 Rich Presence of the friend.
    live_match
        What the friend is doing right now, if anything is worth tracking.
    """

    def __init__(self, user: dota2.User) -> None:
        self.steam: dota2.User = user

        # these get updated with `on_user_update` steamio event
        self.rich_presence: RichPresence = RichPresence.from_steam(user.rich_presence)
        self.live_match: LiveMatch | None = None

    @override
    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} name="{self.steam.name}" id={self.steam.id}>'

    def is_playing_dota(self) -> bool:
        """Whether this friend is playing Dota 2 or not."""
        return bool(app := self.steam.app) and app.id == DOTA_APP_ID


@dataclass
class Player:
    """A player of an active Dota 2 match.

    Attributes
    ----------
    id
        Steam account id.
    player_slot
        Position of the player in the match's player order.
    lifetime_games
        Amount of games the player has played in total.
    rank_display_name
        Human-readable rank medal.
    hero
        Selected hero. Not truthy until the hero is picked.
    """

    id: int
    player_slot: int
    lifetime_games: int
    rank_display_name: str
    hero: Hero

    @override
    def __repr__(self) -> str:
        return f"<Player id={self.id} slot={self.player_slot}>"

    @classmethod
    async def create(cls, bot: Dota2Bot, account_id: int, player_slot: int, hero: Hero) -> Self:
        """Create a player, fetching their profile card.

        Parameters
        ----------
        bot
            The bot to make requests with.
        account_id
            Steam account id of the player.
        player_slot
            Position of the player in the match's player order.
        hero
            Hero the player has selected so far.
        """
        profile_card = await bot.create_partial_user(account_id).dota2_profile_card()
        return cls(
            id=account_id,
            player_slot=player_slot,
            lifetime_games=profile_card.lifetime_games,
            rank_display_name=rank_display_name(profile_card),
            hero=hero,
        )
