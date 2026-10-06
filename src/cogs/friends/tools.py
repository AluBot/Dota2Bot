from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from steam.ext import dota2


def rank_medal_display_name(profile_card: dota2.ProfileCard) -> str:
    """Get human-readable rank medal string out of player's Dota 2 Profile Card."""
    display_name = profile_card.rank_tier.division
    if stars := profile_card.rank_tier.stars:
        display_name += f" \N{BLACK STAR}{stars}"
    if number_rank := profile_card.leaderboard_rank:
        display_name += f" #{number_rank}"
    return display_name
