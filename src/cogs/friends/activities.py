from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Activity:
    """Activity.

    The purpose of this is to group Steam Rich Presence statuses into some activity categories.
    """

    # @override
    # def __str__(self) -> str:
    #     return f"<{self.__class__.__name__}>"


@dataclass(slots=True)
class Incomplete(Activity):
    """Incomplete activity.

    A special type of activity as the bot only processes information from complete activities.
    E.g. the bot waits for "Dashboard" instead of figuring out random "Incomplete('Closed Dota')".
    """

    msg: str


@dataclass(slots=True)
class Dashboard(Activity):
    """Dashboard."""


# Partial Matches
# Partial matches gets checked with bot's current list of active matches.
# If there is an active game under the same id - then we don't need to initialize full match twice, we just use the same one
# If there is no such game - then we try to get the data.
# Yes, we need this structure (I think). I've tried simplifying it a few times.


@dataclass(slots=True)
class UnsupportedPartialMatch(Activity):
    """Unsupported Partial Match.

    Not supported, such as bot games.
    """

    msg: str


@dataclass(slots=True)
class PlayingPartialMatch(Activity):
    """Playing Partial Match."""

    watchable_game_id: str


@dataclass(slots=True)
class SpectatingPartialMatch(Activity):
    """Spectating Partial Match."""

    watching_server: str
