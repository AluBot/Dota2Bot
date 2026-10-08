from __future__ import annotations

from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Self, override

from steam.enums import Enum as SteampyEnum, classproperty

if TYPE_CHECKING:
    from collections.abc import Mapping


class SteampyStrEnum(SteampyEnum, str):
    """An enumeration where all the values are strings, emulates `enum.StrEnum`.

    Note, that Enum class is from `steam` package, not from the `enum` stdlib.
    """

    __slots__ = ()

    if TYPE_CHECKING:

        @override
        def __new__(cls, value: str) -> Self: ...  # ty: ignore[invalid-method-override]

        @override
        @classmethod
        def try_value(cls, value: str) -> Self: ...


class Status(SteampyStrEnum):
    """Enum describing "status" field in Steam's Rich Presence."""

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

    @classproperty
    def KNOWN_DISPLAY_NAMES(cls: type[Self]) -> Mapping[Status, str]:  # pyright: ignore[reportGeneralTypeIssues] # ruff: ignore[invalid-function-name, invalid-first-argument-name-for-method]
        """Mapping between RPStatus enum and human-readable display names for them."""
        return {
            cls.RichPresenceNone: "Offline/Invisible",
            cls.NoStatus: "No Status (yet)",
            cls.Idle: "Main Menu (Idle)",
            cls.MainMenu: "Main Menu",
            cls.Finding: "Finding A Match",
            cls.WaitingToLoad: "Waiting For Players to Load",
            cls.HeroSelection: "Hero Selection",
            cls.Strategy: "Strategy Phase",
            cls.PreGame: "PreGame",
            cls.Playing: "Playing",
            cls.Spectating: "Spectating",
            cls.PrivateLobby: "Private Lobby",
            cls.BotPractice: "Bot Practice",
            cls.Coaching: "Coaching",
            cls.WatchingTournament: "Watching Tournament",
            cls.CustomGameProgress: "Custom Game",
            cls.Crownfall: "Crownfall activity",
            cls.DarkCarnival: "DarkCarnival activity",
            cls.WatchingTI: "Watching TI",
        }

    @property
    def display_name(self) -> str:
        """Chat-send friendly display name, if present."""
        try:
            return self.KNOWN_DISPLAY_NAMES[self]
        except KeyError:
            # will still return "#DEADLOCK_RP_SOMETHING"
            return self.value

    @override
    @classmethod
    def try_value(cls, value: str) -> Status:
        # Special cases
        if value.startswith("#DOTA_Crownfall"):
            return cls.Crownfall
        if value.startswith("#DOTA_DarkCarnival"):
            return cls.DarkCarnival
        # Normal
        return super().try_value(value)


class LobbyParam0(StrEnum):
    """Known Lobby Param 0."""

    DemoMode = "#demo_hero_mode_name"
    BotMatch = "#DOTA_lobby_type_name_bot_match"


class MatchState(IntEnum):
    """Indicates current state for matches."""

    Starting = 1
    Live = 2
    Pending = 3
    Completed = 4
    ApiError = 91
