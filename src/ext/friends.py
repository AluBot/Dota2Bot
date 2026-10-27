"""Friends.

follows the bot's Steam friends through Dota 2 Rich Presence and inspects their live matches.

Notices
-------
* MPL-2.0 License, see LICENSE file for more details.
* Copyright (C) 2020-present @Aluerie.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import logging
import pprint
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Self, override

import steam
from steam.enums import Enum as SteampyEnum
from steam.ext import commands, dota2

from core import Dota2Bot
from shared import clock
from shared.apis.local import MatchState
from shared.concepts import tasks

if TYPE_CHECKING:
    from collections.abc import Callable

    from steam.ext.dota2 import Hero

    from shared.typings import local_api as schemas

log = logging.getLogger(__name__)

DOTA_APP_ID = 570
GC_READY_TIMEOUT = 11 * 60  # seconds the Game Coordinator gets to get ready before the bot is restarted
POLL_INTERVAL = 10.1  # seconds between attempts to get live match data
POLL_ATTEMPTS = 30  # attempts before giving up on a live match
MAX_FAILED_LOOKUPS = 17  # pending matches that failed this many outcome lookups are ignored
MATCH_HISTORY_MAX_AGE = dt.timedelta(hours=48)
LIVE_MATCH_MAX_AGE = dt.timedelta(hours=8)
MATCHES_KEPT_PER_FRIEND = 20
PARTY_MEMBER_RE = re.compile(r"members\s\{\ssteam_id:\s([0-9]+)")


def rank_display_name(profile_card: dota2.ProfileCard) -> str:
    """Get human-readable rank medal string out of player's Dota 2 Profile Card."""
    display_name = profile_card.rank_tier.division
    if stars := profile_card.rank_tier.stars:
        display_name += f" \N{BLACK STAR}{stars}"
    if number_rank := profile_card.leaderboard_rank:
        display_name += f" #{number_rank}"
    return display_name


# --- ACTIVITIES -------------------------------------------------------------------------------------------------------


@dataclass(slots=True)
class Activity:
    """Activity.

    Groups Steam Rich Presence statuses into a few categories the bot cares about.
    """


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
    """Dashboard."""


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
        Parsed "status" field. Not used for comparison, as it is derived from `raw`.
    """

    raw: dict[str, str]
    status: Status = field(compare=False)

    @classmethod
    def from_steam(cls, raw: dict[str, str] | None) -> Self:
        """Create from the raw Rich Presence of a Steam user, which is `None` when they are offline."""
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
    """Turn Rich Presence into an `Activity`."""
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


# --- FRIENDS AND PLAYERS ----------------------------------------------------------------------------------------------


class Friend:
    """A friend from the bot's Steam friend list."""

    def __init__(self, user: dota2.User) -> None:
        self.steam: dota2.User = user

        # these get updated with `on_user_update` steamio event
        self.rich_presence: RichPresence = RichPresence.from_steam(user.rich_presence)
        self.live_match: LiveMatch | None = None

    @override
    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} name="{self.steam.name}" id={self.steam.id}>'

    def is_playing_dota(self) -> bool:
        """Whether this friend is playing dota or not."""
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


# --- MATCHES ----------------------------------------------------------------------------------------------------------


class LiveMatch:
    """Base class for whatever a friend is currently doing in a match.

    Compared to older implementations, `.players` and `.heroes` data live here and not on the player objects.
    """

    def __init__(self, bot: Dota2Bot, tag: schemas.TagLiteral, message: str = "") -> None:
        self.bot: Dota2Bot = bot
        self.tag: schemas.TagLiteral = tag
        self.message: str = message

        self.id: int | None = None
        self.lobby_type: dota2.LobbyType | None = None
        self.game_mode: dota2.GameMode | None = None
        self.server_steam_id: int | None = None
        self.average_mmr: int | None = None
        self.started_at: dt.datetime = clock.utcnow()

        self.players: dict[int, Player] = {}
        self.friends: set[Friend] = set()
        self.state: MatchState = MatchState.Starting

        # set once players and heroes are known
        self.ready: asyncio.Event = asyncio.Event()

    @override
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.id} state={self.state.name}>"

    async def add_friend(self, friend: Friend) -> None:
        """Register a friend as a participant of this match."""
        self.friends.add(friend)


class UnsupportedMatch(LiveMatch):
    """A match the bot can't inspect.

    All chat commands for objects of this type should respond with the message.
    For example, if a friend is playing Demo Mode, then the bot should only respond with "Demo Mode is not supported",
    because, well, there is no data in Demo Mode to inspect.
    """

    def __init__(self, bot: Dota2Bot, message: str) -> None:
        super().__init__(bot, "unsupported", message)


@dataclass(slots=True)
class MatchSnapshot:
    """Match data at one moment in time, regardless of where it came from.

    Attributes
    ----------
    id
        Match id.
    lobby_type
        Lobby type.
    game_mode
        Game mode.
    started_at
        When the match has started.
    players
        `(account id, hero)` pairs in player slot order.
    server_steam_id
        Steam id64 of the game server, if known.
    average_mmr
        Average MMR of the match, if known.
    """

    id: int
    lobby_type: dota2.LobbyType
    game_mode: dota2.GameMode
    started_at: dt.datetime
    players: list[tuple[int, Hero]]
    server_steam_id: int | None = None
    average_mmr: int | None = None


class TrackedMatch(LiveMatch, ABC):
    """A match whose data is polled until the players and their heroes are known.

    Subclasses implement `fetch_snapshot` and call `start` at the end of their `__init__`.
    """

    def start(self) -> None:
        """Start polling for the match data."""
        self._task: asyncio.Task[None] = asyncio.create_task(self._poll())

    def cancel(self) -> None:
        """Stop polling and mark the match as abandoned by the bot."""
        self._task.cancel()
        self.state = MatchState.GaveUp

    @abstractmethod
    async def fetch_snapshot(self) -> MatchSnapshot:
        """Fetch the current match data. Raise any exception if it's not available (yet)."""

    async def on_live(self) -> None:
        """Hook called once, when the match data is complete and right before the match is marked live."""

    def _is_ready(self) -> bool:
        return bool(self.game_mode) and bool(self.players) and all(bool(p.hero) for p in self.players.values())

    async def _poll(self) -> None:
        for attempt in range(POLL_ATTEMPTS):
            if attempt:
                await asyncio.sleep(POLL_INTERVAL)
            try:
                await self._apply(await self.fetch_snapshot())
            except Exception:
                log.debug("%r: attempt %d/%d failed", self, attempt + 1, POLL_ATTEMPTS, exc_info=True)
                continue
            if self._is_ready():
                try:
                    await self.on_live()
                except Exception:
                    log.exception("%r: on_live failed", self)
                self.state = MatchState.Live
                self.ready.set()
                log.debug("%r is ready", self)
                return

        log.warning("%r: gave up getting the match data", self)
        self.state = MatchState.GaveUp

    async def _apply(self, snapshot: MatchSnapshot) -> None:
        self.id = snapshot.id
        self.lobby_type = snapshot.lobby_type
        self.game_mode = snapshot.game_mode
        self.started_at = snapshot.started_at
        self.server_steam_id = snapshot.server_steam_id
        self.average_mmr = snapshot.average_mmr

        # Heroes get picked over time, so known players only get their hero refreshed.
        # Only the new players need their profile cards fetched.
        new_players: dict[int, asyncio.Task[Player]] = {}
        async with asyncio.TaskGroup() as group:
            for slot, (account_id, hero) in enumerate(snapshot.players):
                if (player := self.players.get(account_id)) is not None:
                    player.hero = hero
                else:
                    new_players[account_id] = group.create_task(Player.create(self.bot, account_id, slot, hero))
        self.players.update({account_id: task.result() for account_id, task in new_players.items()})


class SpectatingMatch(TrackedMatch):
    """A match the friend is spectating. The data comes from the Steam Web API."""

    def __init__(self, bot: Dota2Bot, watching_server: str) -> None:
        super().__init__(bot, "spectating")
        self.watching_server: str = watching_server
        # Steam Web API uses Steam IDs for servers in id64 format, but Rich Presence provides them in id3.
        # Example: id3: [A:1:3513917470:30261] -> id64: 90201966066671646.
        steam_id = steam.ID.from_id3(watching_server)
        assert steam_id is not None, "Failed to get Steam ID from id3."
        self.server_steam_id = steam_id.id64
        self.start()

    @override
    async def fetch_snapshot(self) -> MatchSnapshot:
        stats = await self.bot.steam_web.get_real_time_stats(self.server_steam_id)
        return MatchSnapshot(
            id=stats.id,
            lobby_type=stats.lobby_type,
            game_mode=stats.game_mode,
            started_at=stats.started_at,
            players=[(player.id, player.hero) for player in stats.players],
            server_steam_id=self.server_steam_id,
        )


class PlayingMatch(TrackedMatch):
    """A match the friend is playing. The data comes from the Game Coordinator and gets saved to the database."""

    def __init__(self, bot: Dota2Bot, watchable_game_id: str) -> None:
        super().__init__(bot, "playing")
        self.watchable_game_id: str = watchable_game_id
        self.lobby_id: int = int(watchable_game_id)
        self.start()

    @property
    def has_match_history(self) -> bool:
        """Whether Dota keeps a trace of this match.

        Practice lobbies leave none: there is no match to inspect in match history, opendota, etc.,
        and `.minimal()` errors out with `ValueError` for them.
        """
        return self.lobby_type != dota2.LobbyType.Practice

    @override
    async def fetch_snapshot(self) -> MatchSnapshot:
        live = next(iter(await self.bot.live_matches(lobby_ids=[self.lobby_id])), None)
        if live is None:
            msg = f'FindTopSourceTVGames did not find watchable_game_id "{self.watchable_game_id}".'
            raise LookupError(msg)
        return MatchSnapshot(
            id=live.id,
            lobby_type=live.lobby_type,
            game_mode=live.game_mode,
            started_at=live.start_time,
            players=[(player.id, player.hero) for player in live.players],
            server_steam_id=live.server_steam_id,
            average_mmr=live.average_mmr,
        )

    @override
    async def on_live(self) -> None:
        if not self.has_match_history:
            return
        query = """
            INSERT INTO ttv_dota_matches
            (match_id, start_time, lobby_type, game_mode, live)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (match_id) DO NOTHING;
        """
        await self.bot.pool.execute(query, self.id, self.started_at, self.lobby_type, self.game_mode, MatchState.Live)
        # a friend who joins during these inserts gets picked up by the hourly match history check
        for friend in list(self.friends):
            await self._record_friend(friend)

    @override
    async def add_friend(self, friend: Friend) -> None:
        await super().add_friend(friend)
        if self.state == MatchState.Live and self.has_match_history:
            await self._record_friend(friend)

    async def _record_friend(self, friend: Friend) -> None:
        if friend.rich_presence.status == Status.Coaching:
            # The match will not be in a coach's match history, so there is no W/L to track.
            return
        player = self.players.get(friend.steam.id)
        if player is None:
            log.warning("%r: friend %r is not among the match players", self, friend)
            return
        query = """
            INSERT INTO ttv_dota_match_players
            (friend_id, match_id, hero_id, player_slot)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (friend_id, match_id) DO NOTHING;
        """
        await self.bot.pool.execute(query, friend.steam.id, self.id, player.hero.id, player.player_slot)


def _get_or_create[T: LiveMatch](index: dict[str, T], key: str, factory: Callable[[], T]) -> T:
    """Get a match from the index, or create it. Matches the bot gave up on don't get reused."""
    live = index.get(key)
    if live is None or live.state == MatchState.GaveUp:
        live = index[key] = factory()
    return live


# --- COG --------------------------------------------------------------------------------------------------------------


class FriendsCog(commands.Cog[Dota2Bot]):
    """Component with all 9kmmrbot-like Dota 2 features.

    This component uses Dota 2 Rich Presence (RP), Dota 2 web API and Dota 2 Game Coordinator (GC) API calls
    to figure out current state of each friend in the bot's steam friend list.
    If they are currently playing - the bot activates special chat commands inspecting their live match.
    The bot also stores some match history information in the database for some features/commands to use.

    Notes
    -----
    * The restrictions of such approach are
        1. people have to add the bot into their friend list;
        2. people have to play Dota 2 while being green-online in steam.

      The other solution that would solve these problems would be using Dota 2 Game State Integration (GSI),
      which I'm going to implement one day.
    """

    def __init__(self) -> None:
        super().__init__()
        self.play_matches: dict[str, PlayingMatch] = {}  # watchable game id -> match
        self.spectate_matches: dict[str, SpectatingMatch] = {}  # watching server -> match

        self._reported_statuses: set[str] = set()

        self.fill_friends.start()
        self.double_check_gc_match_history.start()
        self.remove_way_too_old_matches.start()

    @override
    async def cog_unload(self) -> None:
        for loop in (
            self.fill_friends,
            self.double_check_gc_match_history,
            self.remove_way_too_old_matches,
            self.process_pending_matches,
        ):
            loop.cancel()
        for index in (self.play_matches, self.spectate_matches):
            for live in index.values():
                if live.state == MatchState.Starting:
                    live.cancel()

    # --- rich presence ---

    def _index_friend(self, steam_friend: steam.Friend) -> Friend:
        """Add a friend of the bot into the index, unless they are there already."""
        friend = self.bot.friends_index.get(steam_friend.id)
        if friend is None:
            # we need `friend._user` because `Friend` class doesn't have Dota 2 features which User class have.
            friend = self.bot.friends_index[steam_friend.id] = Friend(steam_friend._user)  # ruff: ignore[private-member-access]  # ty: ignore[invalid-argument-type]
        return friend

    @tasks.loop(count=1)
    async def fill_friends(self) -> None:
        """Index bot's friend list and analyze their rich presences."""
        log.debug("Indexing bot's friend list.")
        for steam_friend in await self.bot.user.friends():
            self._index_friend(steam_friend)

        # From now on `on_user_update` handles changes, so none get lost while the initial analysis is running.
        self.bot.friend_index_ready.set()
        log.debug("Friends index ready.")

        for friend in list(self.bot.friends_index.values()):
            await self.analyze_rich_presence(friend)
        self._start_pending_matches_loop()  # picks up matches left pending by the previous run

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_user_update(self, _: dota2.User, after: dota2.User) -> None:
        """Event when a steam user is updated, due to one or more of their attributes changing."""
        if not self.bot.friend_index_ready.is_set():
            return  # `fill_friends` is going to analyze everyone once it's done

        rich_presence = RichPresence.from_steam(after.rich_presence)
        friend = self.bot.friends_index.get(after.id)
        if friend is None:
            friend = self.bot.friends_index[after.id] = Friend(after)
        elif friend.rich_presence == rich_presence:
            return

        friend.rich_presence = rich_presence
        log.debug("Recognized rich presence update for %r: %r", friend, rich_presence)
        await self.analyze_rich_presence(friend)

    async def analyze_rich_presence(self, friend: Friend) -> None:
        """Analyze Rich Presence.

        Central function for this component.
        The bot tracks friends' game-flow via inspecting their rich presence.
        If the bot detects a new game being played or watched - it will do necessary actions to
        prepare those matches for further inspection.

        Warning
        -------
        The code in this function is also quite volatile. Valve are quite inconsistent in their Rich Presence data,
        so the logic might break any day.

        Parameters
        ----------
        friend
            The friend whose Rich Presence has changed.
        """
        if not friend.is_playing_dota():
            await self.conclude_match(friend)
            return

        query = "UPDATE ttv_dota_accounts SET last_seen = $1 WHERE friend_id = $2;"
        await self.bot.pool.execute(query, clock.utcnow(), friend.steam.id)

        match classify(friend.rich_presence):
            case Dashboard():
                await self.conclude_match(friend)
            case PlayingPartialMatch(watchable_game_id=game_id):
                live = _get_or_create(self.play_matches, game_id, lambda: PlayingMatch(self.bot, game_id))
                await self._attach(friend, live)
            case SpectatingPartialMatch(watching_server=server):
                live = _get_or_create(self.spectate_matches, server, lambda: SpectatingMatch(self.bot, server))
                await self._attach(friend, live)
            case UnsupportedPartialMatch(msg=msg):
                current = friend.live_match
                if not (isinstance(current, UnsupportedMatch) and current.message == msg):
                    await self._attach(friend, UnsupportedMatch(self.bot, msg))
            case Unrecognized(status=status):
                await self._report_unrecognized(friend, status)
            case _:
                # Incomplete activities - wait for confirmed activities.
                pass

    async def _attach(self, friend: Friend, live: LiveMatch) -> None:
        if friend.live_match is not live:
            await self.conclude_match(friend)
        friend.live_match = live
        await live.add_friend(friend)

    async def _report_unrecognized(self, friend: Friend, status: str) -> None:
        if status in self._reported_statuses:
            return
        self._reported_statuses.add(status)
        rich_presence = friend.rich_presence
        text = (
            "Uncategorized Rich Presence Status\n"
            f"friend=`{friend!r}`\n"
            f"status=`{rich_presence.status.value}`\n"
            f"rich_presence.raw=```json\n{pprint.pformat(rich_presence.raw)}```"
        )
        log.warning(text)
        await self.bot.notify_developers(text)

    # --- pending matches ---

    async def conclude_match(self, friend: Friend) -> None:
        """Detach the friend from their match, and conclude the match if they were the last one in it.

        A live match becomes pending, meaning that the bot is waiting for its outcome.
        A match that never got its data gets abandoned.

        Parameters
        ----------
        friend
            The friend who has left the match.
        """
        live = friend.live_match
        friend.live_match = None
        if live is None:
            return
        live.friends.discard(friend)
        if live.friends:
            return

        if isinstance(live, TrackedMatch) and live.state == MatchState.Starting:
            # The lobby terminated before heroes were picked.
            live.cancel()
        elif isinstance(live, PlayingMatch) and live.state == MatchState.Live:
            live.state = MatchState.Pending
            query = "UPDATE ttv_dota_matches SET live = $1 WHERE match_id = $2;"
            await self.bot.pool.execute(query, MatchState.Pending, live.id)
            self._start_pending_matches_loop()

    def _start_pending_matches_loop(self) -> None:
        if not self.process_pending_matches.is_running():
            self.process_pending_matches.start()

    @tasks.loop(seconds=20)
    async def process_pending_matches(self) -> None:
        """Find out the outcome of pending matches. Stops itself when there are none left.

        Notes
        -----
        * We use match history endpoint specifically because it has `.abandon` attribute.
          So if we remove that quirk - we can probably be fine with just "minimal_match" data.
        * Another option is to use opendota api (stratz is too slow -
          they don't allow access to data until full parse is done)
        """
        query = "SELECT match_id FROM ttv_dota_matches WHERE outcome IS NULL AND live = $1 AND failed < $2;"
        rows = await self.bot.pool.fetch(query, MatchState.Pending, MAX_FAILED_LOOKUPS)
        if not rows:
            self.process_pending_matches.stop()
            return

        for row in rows:
            match_id = row["match_id"]
            try:
                # If friend disconnects before ancient falls (e.g., preemptive disconnects or
                # when game is "Safe to leave") then `.minimal` won't give any results as the game
                # is still live but friend's RP is different. This also happens if friend
                # disconnects-reconnects in the middle of the match.
                minimal = await self.bot.create_partial_match(match_id).minimal()
            except ValueError:
                # matches that failed too many times get ignored by the query above
                query = "UPDATE ttv_dota_matches SET failed = failed + 1 WHERE match_id = $1;"
                await self.bot.pool.execute(query, match_id)
                continue

            query = "UPDATE ttv_dota_matches SET outcome = $1, live = $3 WHERE match_id = $2;"
            await self.bot.pool.execute(query, minimal.outcome, match_id, MatchState.Completed)

    # --- match history and database care ---

    @tasks.loop(hours=1)
    async def double_check_gc_match_history(self) -> None:
        """Double check match history in case missed some games.

        Useful to keep W-L as precise as possible. Also the only source of the `abandon` flag.
        """
        for friend in list(self.bot.friends_index.values()):
            await self._sync_history(friend)

    async def _sync_history(self, friend: Friend) -> None:
        """Add recent matches from the friend's match history into the database."""
        try:
            history = await friend.steam.match_history()
        except Exception:
            log.exception("Failed to get match history of %r", friend)
            return
        for entry in history:
            try:
                await self._store_history_match(entry, friend.steam.id)
            except Exception:
                log.exception("Failed to store match %s of %r", entry.id, friend)

    async def _store_history_match(self, entry: dota2.MatchHistoryMatch, friend_id: int) -> None:
        if entry.start_time < clock.utcnow() - MATCH_HISTORY_MAX_AGE:
            return

        # Fast path: a match we already know as completed only needs its `abandon` flag refreshed,
        # which comes from the match history entry. No need to ask for `.minimal()` again.
        query = """
            UPDATE ttv_dota_match_players mp
            SET abandon = $3
            FROM ttv_dota_matches m
            WHERE m.match_id = mp.match_id AND mp.friend_id = $1 AND mp.match_id = $2 AND m.live = $4;
        """
        status = await self.bot.pool.execute(query, friend_id, entry.id, entry.abandon, MatchState.Completed)
        if status == "UPDATE 1":
            return

        # match history entities on their own do not give proper outcome (Radiant/Dire)
        minimal = await self.bot.create_partial_match(entry.id).minimal()
        player_slot = next((slot for slot, player in enumerate(minimal.players) if player.hero == entry.hero), None)
        if player_slot is None:
            msg = f"Hero of match history match {entry.id} is not among the match players."
            raise LookupError(msg)

        query = """
            INSERT INTO ttv_dota_matches
                (match_id, start_time, lobby_type, game_mode, outcome, live)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (match_id) DO
                UPDATE SET live = $6;
        """
        await self.bot.pool.execute(
            query,
            entry.id,
            entry.start_time,
            entry.lobby_type,
            entry.game_mode,
            minimal.outcome,
            MatchState.Completed,
        )
        query = """
            INSERT INTO ttv_dota_match_players
            (friend_id, match_id, hero_id, player_slot, abandon)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (friend_id, match_id) DO
                UPDATE SET abandon = $5;
        """
        await self.bot.pool.execute(query, friend_id, entry.id, entry.hero.id, player_slot, entry.abandon)

    @tasks.loop(hours=6)
    async def remove_way_too_old_matches(self) -> None:
        """Clean up old matches from the database and from the bot's memory.

        Only the latest matches of each friend are kept in the database; matches that are still live or pending
        are never removed. Matches that started more than 8 hours ago are forgotten by the in-memory indexes.
        """
        log.debug("Removing way too old matches.")
        query = """
            WITH ranked AS (
                SELECT mp.match_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY mp.friend_id
                           ORDER BY m.start_time DESC, m.match_id DESC
                       ) AS rn
                FROM ttv_dota_match_players mp
                JOIN ttv_dota_matches m ON m.match_id = mp.match_id
            )
            DELETE FROM ttv_dota_matches
            WHERE live = $1
              AND match_id NOT IN (SELECT match_id FROM ranked WHERE rn <= $2);
        """
        await self.bot.pool.execute(query, MatchState.Completed, MATCHES_KEPT_PER_FRIEND)

        cut_off = clock.utcnow() - LIVE_MATCH_MAX_AGE
        for index in (self.play_matches, self.spectate_matches):
            for key in [key for key, live in index.items() if live.started_at < cut_off]:
                del index[key]

    # --- task care ---

    @fill_friends.before_loop
    async def wait_for_clients(self) -> None:
        """Wait for all the needed clients to get ready.

        Otherwise, such things as Dota 2 Coordinator requests are going to error out.
        Unfortunately, specifically, Dota 2 Coordinator usually takes ~30 seconds to get ready.

        Also a crutch for the infamous Tuesday Steam maintenance problems: when steam crashes the bot never
        properly reconnects or restarts on its own, so if the Coordinator is not ready in time - restart the bot.
        """
        await self.bot.wait_until_ready()
        try:
            async with asyncio.timeout(GC_READY_TIMEOUT):
                await self.bot.wait_until_gc_ready()
        except TimeoutError:
            log.critical("🔴 Failed to wait for Dota 2 Game Coordinator to get ready - restarting the bot. 🔴")
            try:
                await asyncio.create_subprocess_exec("sudo", "systemctl", "restart", "dota2bot")
            except OSError:
                log.exception("Failed to restart the bot's process")
            await self.bot.wait_until_gc_ready()

    @double_check_gc_match_history.before_loop
    @remove_way_too_old_matches.before_loop
    async def wait_for_friend_index(self) -> None:
        """Wait for `fill_friends`, which itself waits for all the clients, to fill the friend index."""
        await self.bot.friend_index_ready.wait()

    # --- invites and friends ---

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_invite(self, invite: steam.Invite) -> None:
        """Accept user invites that the bot has asked for within the last 10 minutes, decline everything else."""
        log.info("Received invite: %r", invite)
        if not isinstance(invite, steam.UserInvite):
            method = getattr(invite, "decline", None)
            if inspect.iscoroutinefunction(method):
                await method()
            return

        sent_dt, _ = self.bot.pending_invites.get(invite.author.id, (None, None))
        if sent_dt is not None and clock.utcnow() - sent_dt < dt.timedelta(minutes=10):
            await invite.accept()

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_invite_accept(self, invite: steam.Invite) -> None:
        """Log accepted invites."""
        log.info("Accepted invite: %r", invite)

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_invite_decline(self, invite: steam.Invite) -> None:
        """Log declined invites."""
        log.info("Declined invite: %r", invite)

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_friend_add(self, steam_friend: steam.Friend) -> None:
        """Save the new friend's account, index them and sync their match history."""
        log.info("Added friend %r", steam_friend)
        query = (
            "INSERT INTO ttv_dota_accounts (friend_id, twitch_id) VALUES ($1, $2) ON CONFLICT (friend_id) DO NOTHING;"
        )
        _, twitch_id = self.bot.pending_invites.pop(steam_friend.id, (None, None))
        await self.bot.pool.execute(query, steam_friend.id, twitch_id)

        friend = self._index_friend(steam_friend)
        await self.analyze_rich_presence(friend)
        await self.bot.wait_until_gc_ready()
        await self._sync_history(friend)


async def setup(bot: Dota2Bot) -> None:
    """Load Dota2Bot's extension. Framework of steamio."""
    await bot.add_cog(FriendsCog())
