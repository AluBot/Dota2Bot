# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Rich Presence.

Parses Steam Rich Presence of Dota 2 into statuses and groups those into a few activities the bot cares about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Self, override

import steam

from utils.const import SteampyStrEnum

__all__ = (
    "Activity",
    "Dashboard",
    "Incomplete",
    "PlayingPartialMatch",
    "RichPresence",
    "SpectatingPartialMatch",
    "Status",
    "Unrecognized",
    "UnsupportedPartialMatch",
    "classify",
)

PARTY_MEMBER_RE = re.compile(r"members\s\{\ssteam_id:\s([0-9]+)")


# --- ACTIVITIES -------------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Activity:
    """Base class for the categories the bot sorts Steam Rich Presence statuses into."""


@dataclass(slots=True)
class Incomplete(Activity):
    """Not enough information yet.

    The bot only reacts to complete activities, e.g. it waits for "Dashboard" instead of reacting to "Closed Dota".
    """

    msg: str


@dataclass(slots=True)
class Unrecognized(Activity):
    """Rich Presence status the bot has never seen. The developers get notified about those."""

    status: str


@dataclass(slots=True)
class Dashboard(Activity):
    """The friend is somewhere in the Dota 2 main menu, i.e. not in a match."""


@dataclass(slots=True)
class UnsupportedPartialMatch(Activity):
    """Match of a kind the bot can't inspect, such as a bot game."""

    msg: str


@dataclass(slots=True)
class PlayingPartialMatch(Activity):
    """Playing a match; the bot still has to find out the rest of its data."""

    watchable_game_id: str


@dataclass(slots=True)
class SpectatingPartialMatch(Activity):
    """Spectating a match; the bot still has to find out the rest of its data."""

    watching_server: str


# --- RICH PRESENCE ----------------------------------------------------------------------------------------------------


class Status(SteampyStrEnum):
    """Enum describing the "status" field in Steam's Rich Presence."""

    # MY OWN ADDITIONS
    RichPresenceNone = "#MY_RP_NONE"  # Rich Presence is None
    NoStatus = "#MY_NO_STATUS"  # Somehow "status" field is missing from Rich Presence

    # SPECIAL CASES
    Crownfall = "#SPECIAL_CROWNFALL"
    DarkCarnival = "#SPECIAL_DARKCARNIVAL"

    # DOTA_RP
    Idle = "#DOTA_RP_IDLE"
    MainMenu = "#DOTA_RP_INIT"
    Finding = "#DOTA_RP_FINDING_MATCH"
    WaitingForMapToLoad = "#DOTA_RP_WAIT_FOR_MAP_TO_LOAD"
    WaitingToLoad = "#DOTA_RP_WAIT_FOR_PLAYERS_TO_LOAD"
    HeroSelection = "#DOTA_RP_HERO_SELECTION"
    Strategy = "#DOTA_RP_STRATEGY_TIME"
    PreGame = "#DOTA_RP_PRE_GAME"
    Playing = "#DOTA_RP_PLAYING_AS"
    Spectating = "#DOTA_RP_SPECTATING"
    PrivateLobby = "#DOTA_RP_PRIVATE_LOBBY"
    BotPractice = "#DOTA_RP_BOTPRACTICE"  # cSpell: ignore BOTPRACTICE
    Coaching = "#DOTA_RP_COACHING"
    WatchingTournament = "#DOTA_RP_WATCHING_TOURNAMENT"
    CustomGameProgress = "#DOTA_RP_GAME_IN_PROGRESS_CUSTOM"
    CustomGameLobby = "#DOTA_RP_LOBBY_CUSTOM"
    CoopBot = "#DOTA_RP_COOPBOT"  # cSpell: ignore COOPBOT
    WatchingTI = "#DOTA_RP_WATCHING_TI"

    @property
    def display_name(self) -> str:
        """Chat-send friendly display name. Falls back to the raw status value."""
        return _DISPLAY_NAMES.get(self, self.value)

    @override
    @classmethod
    def try_value(cls, value: str) -> Status:
        for status, starts in _STARTSWITH_MAPPING.items():
            if value.startswith(starts):
                return status
        return super().try_value(value)


_DISPLAY_NAMES: dict[Status, str] = {
    Status.RichPresenceNone: "Offline/Invisible",
    Status.NoStatus: "No Status (yet)",
    Status.Idle: "Main Menu (Idle)",
    Status.MainMenu: "Main Menu",
    Status.Finding: "Finding A Match",
    Status.WaitingForMapToLoad: "Waiting For Map to Load",
    Status.WaitingToLoad: "Waiting For Players to Load",
    Status.HeroSelection: "Hero Selection",
    Status.Strategy: "Strategy Phase",
    Status.PreGame: "PreGame",
    Status.Playing: "Playing",
    Status.Spectating: "Spectating",
    Status.PrivateLobby: "Private Lobby",
    Status.BotPractice: "Bot Practice",
    Status.Coaching: "Coaching",
    Status.WatchingTournament: "Watching Tournament",
    Status.CustomGameProgress: "Custom Game",
    Status.CustomGameLobby: "Custom Game Lobby",
    Status.CoopBot: "Coop vs Bots",
    Status.Crownfall: "Crownfall activity",
    Status.DarkCarnival: "DarkCarnival activity",
    Status.WatchingTI: "Watching TI",
}

_STARTSWITH_MAPPING: dict[Status, str] = {
    Status.Crownfall: "#DOTA_Crownfall",
    Status.DarkCarnival: "#DOTA_DarkCarnival",
}


class LobbyParam0(StrEnum):
    """Known Lobby Param 0."""

    DemoMode = "#demo_hero_mode_name"
    BotMatch = "#DOTA_lobby_type_name_bot_match"


# `param1` is usually a hero level, which is pointless for the game-flow logic to know.
_IGNORED_RICH_PRESENCE_KEYS = frozenset({"param1"})


@dataclass(slots=True)
class RichPresence:
    """Rich Presence.

    Normally Rich Presence is just a dictionary of data. This adds the parsed status and ignores noisy keys,
    so two instances are equal when nothing meaningful changed.

    Attributes
    ----------
    raw
        Rich Presence data without the ignored keys.
    status
        Parsed "status" field. Not used for comparison, as it is derived from ``raw``.
    """

    raw: dict[str, str]
    status: Status = field(compare=False)

    @classmethod
    def from_steam(cls, raw: dict[str, str] | None) -> Self:
        """Create from the raw Rich Presence of a Steam user.

        Parameters
        ----------
        raw
            Rich Presence data of a Steam user. It is ``None`` when the user is offline.
        """
        data = {k: v for k, v in (raw or {}).items() if k not in _IGNORED_RICH_PRESENCE_KEYS}
        if not data:
            return cls(data, Status.RichPresenceNone)
        return cls(data, Status.try_value(data.get("status", Status.NoStatus.value)))

    @override
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} status={self.status.name}>"

    @property
    def party_ids(self) -> list[int]:
        """Account ids of everyone in the player's party, the player included."""
        # Valve slices a big party string into `party2`
        text = self.raw.get("party", "") + self.raw.get("party2", "")
        return [steam.ID(int(m)).id for m in PARTY_MEMBER_RE.findall(text)]


_DASHBOARD_STATUSES = frozenset({
    Status.Idle,
    Status.MainMenu,
    Status.Finding,
})
_PLAYING_STATUSES = frozenset({
    Status.WaitingToLoad,
    Status.HeroSelection,
    Status.Strategy,
    Status.PreGame,
    Status.WaitingForMapToLoad,
    Status.Playing,
    Status.Coaching,
})
_WATCHING_STATUSES = frozenset({
    Status.Spectating,
    Status.WatchingTournament,
    Status.WatchingTI,
})
_UNSUPPORTED_STATUSES: dict[Status, str] = {
    Status.BotPractice: "Bot matches are not supported",
    Status.PrivateLobby: "Private lobbies (this includes draft in public lobbies) are not supported",
    Status.CustomGameProgress: "Custom games are not supported",
    Status.CustomGameLobby: "Custom games are not supported",
    Status.Crownfall: "Crownfall activities are not supported",
    Status.DarkCarnival: "Dark Carnival activities are not supported",
    Status.CoopBot: "Coop vs Bots matches are not supported",
}


def _classify_playing(rp: RichPresence) -> Activity:
    match rp.raw.get("WatchableGameID"):
        case None:
            # something is off, check whether it's one of the lobbies without a watchable game
            match rp.raw.get("param0"):
                case LobbyParam0.DemoMode:
                    return UnsupportedPartialMatch("Demo mode is not supported")
                case LobbyParam0.BotMatch:
                    return UnsupportedPartialMatch("Bot matches are not supported")
                case _:
                    return Incomplete("Unknown (Rich Presence is 'playing' but watchable_game_id is None)")
        case "0":
            # usually this happens when a player has just quit the match into the main menu,
            # the status flickers for a few seconds to be `watchable_game_id=0`
            return Incomplete("Unknown (Rich Presence is 'playing' but watchable_game_id=0)")
        case watchable_game_id:
            return PlayingPartialMatch(watchable_game_id)


def classify(rp: RichPresence) -> Activity:
    """Turn Rich Presence into an ``Activity``.

    Parameters
    ----------
    rp
        Rich Presence to classify.

    Returns
    -------
    Activity
        The category of what the friend is doing. ``Incomplete`` means "wait for a more definite status".
    """
    status = rp.status
    if status in _DASHBOARD_STATUSES:
        return Dashboard()
    if status in _PLAYING_STATUSES:
        return _classify_playing(rp)
    if status in _WATCHING_STATUSES:
        watching_server = rp.raw.get("watching_server")
        if watching_server is None:
            # I think this happens only for watching replays
            return UnsupportedPartialMatch("Watching replays is not supported")
        return SpectatingPartialMatch(watching_server)
    if status == Status.NoStatus:
        # usually this happens in exact moment when the player closes Dota
        return Incomplete("Closed Dota")
    if status == Status.RichPresenceNone:
        return Incomplete("No Rich Presence yet")
    if (msg := _UNSUPPORTED_STATUSES.get(status)) is not None:
        return UnsupportedPartialMatch(msg)
    return Unrecognized(status.value)
