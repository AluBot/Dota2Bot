from __future__ import annotations

import asyncio
import datetime as dt
import logging
import pprint
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Self, override

import steam
from steam.enums import Enum as SteampyEnum, classproperty
from steam.ext import commands, dota2

from core import Dota2Bot
from shared import clock, errors
from shared.apis.local import MatchState
from shared.concepts import tasks

if TYPE_CHECKING:
    import datetime

    from steam.ext.dota2 import Hero

    from shared.types_ import local_api as schemas
if TYPE_CHECKING:
    from collections.abc import Mapping

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


#############################################################################################################################
# UTILITIES
#############################################################################################################################


def rank_display_name(profile_card: dota2.ProfileCard) -> str:
    """Get human-readable rank medal string out of player's Dota 2 Profile Card."""
    display_name = profile_card.rank_tier.division
    if stars := profile_card.rank_tier.stars:
        display_name += f" \N{BLACK STAR}{stars}"
    if number_rank := profile_card.leaderboard_rank:
        display_name += f" #{number_rank}"
    return display_name


#############################################################################################################################
# ACTIVITIES
#############################################################################################################################


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


#############################################################################################################################
# ENUMS
#############################################################################################################################


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


#############################################################################################################################
# MODELS
#############################################################################################################################


class RichPresence:
    """Rich Presence.

    Normally Rich Presence is just a dictionary of data.
    This class adds some utility for GameFlow component to use.
    """

    def __init__(self, raw: dict[str, str] | None) -> None:
        self.raw: dict[str, str] = raw or {}
        self.status: Status = (
            Status.try_value(value=self.raw.get("status", "#MY_NO_STATUS")) if self.raw else Status.RichPresenceNone
        )

    @override
    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} status={self.status.name}>"

    @override
    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RichPresence):
            return False

        # we need to exclude `param1` from comparison because for Dota 2 Rich Presence it's usually a hero level
        # which is pointless for the game-flow logic to know.
        # SO.com - Compare dictionaries ignoring specific keys: https://stackoverflow.com/a/70145635/19217368
        ignore_keys: set[str] = {"param1"}
        return ignore_keys.issuperset(k for (k, _) in other.raw.items() ^ self.raw.items())

    @override
    def __hash__(self) -> int:
        return hash(self.raw)


class Friend:
    """Friend."""

    def __init__(self, bot: Dota2Bot, steam: dota2.User) -> None:
        self._bot: Dota2Bot = bot
        self.steam: dota2.User = steam

        # Activity related attributes
        # later they all gets updated with `on_user_update` steamio event
        self.rich_presence: RichPresence = RichPresence(steam.rich_presence)
        self.activity: Activity = Incomplete("The bot has not received any Rich Presence information yet")
        self.live_match: LiveMatch | None = None

    def is_playing_dota(self) -> bool:
        """Whether this friend is playing dota or not."""
        return bool(app := self.steam.app) and app.id == 570

    async def update_activity(self) -> bool:
        """Update `self.activity`.

        Return bool indicating whether a new activity was detected.
        """
        new_activity = await self.get_activity()
        if new_activity != self.activity:
            self.activity = new_activity
            return True
        return False

    async def get_activity(self) -> Activity:
        """Get a new value for `self.activity`."""

        rp = self.rich_presence

        # Dashboard
        if rp.status in {Status.Idle, Status.MainMenu, Status.Finding}:
            return Dashboard()

        # Playing
        if rp.status in {
            Status.WaitingToLoad,
            Status.HeroSelection,
            Status.Strategy,
            Status.PreGame,
            Status.WaitingForMapToLoad,
            Status.Playing,
            Status.Coaching,
        }:
            watchable_game_id = rp.raw.get("WatchableGameID")
            if watchable_game_id is None:
                # something is off
                lobby_param0 = rp.raw.get("param0", "_missing")
                return {
                    LobbyParam0.DemoMode: UnsupportedPartialMatch("Demo mode is not supported"),
                    LobbyParam0.BotMatch: UnsupportedPartialMatch("Bot matches are not supported"),
                }.get(
                    lobby_param0,
                    Incomplete("Unknown (Rich Presence is 'playing' but watchable_game_id is None)"),
                )
            if watchable_game_id == "0":
                # something is off again
                # usually this happens when a player has just quit the match into the main menu
                # the status flickers for a few seconds to be `watchable_game_id=0`
                return Incomplete("Unknown (Rich Presence is 'playing' but watchable_game_id=0)")
            return PlayingPartialMatch(watchable_game_id)

        # Watching
        if rp.status in {
            Status.Spectating,
            Status.WatchingTournament,
            Status.WatchingTI,
        }:
            watching_server = rp.raw.get("watching_server")
            if watching_server is None:
                # something is off, but I think this happens only for watching replays
                return UnsupportedPartialMatch("Watching replays is not supported")
            return SpectatingPartialMatch(watching_server)

        # Closed Dota?
        if rp.status == Status.NoStatus:
            # usually this happens in exact moment when the player closes Dota
            return Incomplete("Closed Dota")

        # Other statuses
        other_statuses = {
            Status.BotPractice: "Demo mode is not supported",
            Status.PrivateLobby: "Bot matches are not supported",
            Status.CustomGameProgress: "Custom games are not supported",
            Status.CustomGameLobby: "Private lobbies (this includes draft in public lobbies) are not supported",
            Status.Crownfall: "Crownfall activities are not supported.",
            Status.DarkCarnival: "Dark Carnival activities are not supported.",
            Status.CoopBot: "Coop vs Bots matches are not supported",
        }
        if (msg := other_statuses.get(rp.status)) is not None:
            return UnsupportedPartialMatch(msg)

        # Unrecognized
        # sending warning to @Irene in the discord
        text = (
            f"Uncategorized Rich Presence Status \n"
            f"friend=`{self!r}`\n"
            f"status=`{rp.status.value}`\n"
            "rich_presence.raw="
            f"```json\n{pprint.pformat(rp.raw)}```"
        )
        log.warning(text)
        await self._bot.notify_developers(text)

        return Incomplete("Unknown ('_get_activity' got lost)")

    @override
    def __repr__(self) -> str:
        return f'<{self.__class__.__name__} name="{self.steam.name}" id={self.steam.id}>'


@dataclass
class Player:
    """A class representing an active Dota 2 match player.

    Notes
    -----
    * Compared to my all previous implementations of this - the current iteration for Player class **_DOES NOT_**
        have any data about selected hero or any logic to assign a hero to the player.
        I found it's better to keep `.players` and `.heroes` data bound to `Match` classes.
        The order is carefully taken care of anyway.
    """

    id: int
    player_slot: int
    lifetime_games: int
    rank_display_name: str
    hero: Hero

    @override
    def __repr__(self) -> str:
        return f"<Player id={self.id} slot={self.player_slot}>"

    def __bool__(self) -> bool:
        return bool(self.id)

    @classmethod
    async def create(cls, bot: Dota2Bot, account_id: int, player_slot: int, hero: Hero) -> Player:
        partial_user = bot.create_partial_user(account_id)
        profile_card = await partial_user.dota2_profile_card()

        return Player(
            id=account_id,
            player_slot=player_slot,
            lifetime_games=profile_card.lifetime_games,
            rank_display_name=rank_display_name(profile_card),
            hero=hero,
        )


class LiveMatch:
    def __init__(self, bot: Dota2Bot, tag: schemas.TagLiteral, message: str = "") -> None:
        self.bot: Dota2Bot = bot
        self.tag: schemas.TagLiteral = tag
        self.message: str = message

        # Common match data
        self.id: int | None = None
        self.lobby_type: dota2.LobbyType | None = None
        self.game_mode: dota2.GameMode | None = None
        self.server_steam_id: int | None = None

        # players
        self.players: dict[int, Player] = {}

        # ready events
        self.players_data_ready: asyncio.Event = asyncio.Event()
        self.heroes_data_ready: asyncio.Event = asyncio.Event()

        # other
        self.streamers: set[Friend] = set()
        self.started_at: datetime.datetime = clock.utcnow()
        self.average_mmr: int | None = None

        self.state: MatchState = MatchState.Starting

    def _is_players_data_ready(self) -> bool:
        """A condition to check whether match player data is filled properly."""
        return bool(self.game_mode) and all(bool(player) for player in self.players.values())

    def _is_heroes_data_ready(self) -> bool:
        """A condition to check whether match hero data is filled properly."""
        return bool(self.players) and all(bool(p.hero) for p in self.players.values())


class SpectatingMatch(LiveMatch):
    def __init__(self, bot: Dota2Bot, watching_server: str) -> None:
        super().__init__(bot, tag="spectating")
        self.watching_server: str = watching_server
        # Steam Web API uses Steam IDs for servers in id64 format, but Rich Presence provides them in id3.
        # Example: id3: [A:1:3513917470:30261] -> id64: 90201966066671646.
        steam_id = steam.ID.from_id3(watching_server)
        assert steam_id is not None, "Failed to get Steam ID from id3."
        self.server_steam_id: int = steam_id.id64  # ty: ignore[invalid-mutable-override]
        self.update_data.start()

    @tasks.loop(seconds=10.1, count=30)
    async def update_data(self) -> None:
        log.debug('Updating %s data for watching_server "%s"', self.__class__.__name__, self.watching_server)
        try:
            stats = await self.bot.web_api.get_real_time_stats(self.server_steam_id)
        except errors.ApiError:
            # If SteamWebAPI didn't respond with any data then we have no way to get the data
            self.state = MatchState.ApiError
            self.update_data.stop()
            return

        if not self.players_data_ready.is_set():
            self.id = stats.id
            self.lobby_type = stats.lobby_type
            self.game_mode = stats.game_mode
            self.started_at = stats.started_at
            self.players = {
                player.id: await Player.create(self.bot, player.id, player_slot, player.hero)
                for player_slot, player in enumerate(stats.players)
            }
            if self._is_players_data_ready():
                self.players_data_ready.set()
                log.debug('%s players data ready for: "%s"', self.__class__.__name__, self.watching_server)
                # self.bot.dispatch("players_data_ready", self)
            if self._is_heroes_data_ready():
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watching_server)
                self.heroes_data_ready.set()

        if not self.heroes_data_ready.is_set():
            for player in stats.players:
                self.players[player.id].hero = player.hero
            if self._is_heroes_data_ready():
                self.heroes_data_ready.set()
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watching_server)
                # self.bot.dispatch("heroes_data_ready", self)

        if self.players_data_ready.is_set() and self.heroes_data_ready.is_set():
            self.state = MatchState.Live
            self.update_data.stop()


class PlayingMatch(LiveMatch):
    def __init__(self, bot: Dota2Bot, watchable_game_id: str) -> None:
        super().__init__(bot, "playing")
        self.watchable_game_id: str = watchable_game_id
        self.lobby_id: int = int(watchable_game_id)

        self.average_mmr: int | None = None

        self.update_data.start()

    @tasks.loop(seconds=10.1, count=30)
    async def update_data(self) -> None:
        log.debug('Updating %s data for watchable_game_id "%s"', self.__class__.__name__, self.watchable_game_id)
        match = next(iter(await self.bot.live_matches(lobby_ids=[self.lobby_id])), None)
        assert match, f'FindTopSourceTVGames did not find watchable_game_id "{self.watchable_game_id}".'

        if not self.players_data_ready.is_set():
            self.server_steam_id = match.server_steam_id
            self.id = match.id
            self.average_mmr = match.average_mmr
            self.lobby_type = match.lobby_type
            self.game_mode = match.game_mode
            self.started_at = match.start_time

            self.players = {
                gc_player.id: await Player.create(self.bot, gc_player.id, player_slot, gc_player.hero)
                for player_slot, gc_player in enumerate(match.players)
            }
            if self._is_players_data_ready():
                self.players_data_ready.set()
                log.debug('%s players data ready for: "%s"', self.__class__.__name__, self.watchable_game_id)
                # self.bot.dispatch("players_data_ready", self)
            if self._is_heroes_data_ready():
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watchable_game_id)
                self.heroes_data_ready.set()

        if not self.heroes_data_ready.is_set():
            for player in match.players:
                self.players[player.id].hero = player.hero
            if self._is_heroes_data_ready():
                self.heroes_data_ready.set()
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watchable_game_id)
                # self.bot.dispatch("heroes_data_ready", self)

        if self.players_data_ready.is_set() and self.heroes_data_ready.is_set():
            self.state = MatchState.Live

            # add to the database
            if self.lobby_type != dota2.LobbyType.Practice:
                # These lobby types do not leave any trace for match history purposes
                # I.e. after playing in a practice lobby - there is
                # no match to inspect in match history, opendota, etc;
                # And `match.minimal()` errors out with `ValueError`

                query = """
                    INSERT INTO ttv_dota_matches
                    (match_id, start_time, lobby_type, game_mode, live)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (match_id) DO NOTHING;
                """
                await self.bot.pool.execute(query, self.id, match.start_time, self.lobby_type, self.game_mode, self.state)

                for streamer in self.streamers:
                    if streamer.rich_presence.status == Status.Coaching:
                        # If the person is coaching then the match will not be in their match history -
                        # we don't need to track WinLoss or etc ; no need to add it into the database ;
                        continue

                    player_slot = next((s for (s, p) in enumerate(match.players) if p.id == streamer.steam.id), None)
                    assert player_slot is not None, "Somehow 'player_slot' is 'None' in 'conclude_friend_match'"

                    hero = match.heroes[player_slot]
                    query = """
                        INSERT INTO ttv_dota_match_players
                        (friend_id, match_id, hero_id, player_slot)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (friend_id, match_id) DO NOTHING;
                    """
                    await self.bot.pool.execute(query, streamer.steam.id, self.id, hero.id, player_slot)

            self.update_data.stop()


class UnsupportedMatch(LiveMatch):
    """A class describing unsupported matches.

    All chat commands for objects of this type should return unsupported message response.
    For example, if streamer is playing Demo Mode, then the bot should only respond with "Demo Mode is not supported",
    because, well, there is no data in Demo Mode to insect.
    """

    def __init__(self, bot: Dota2Bot, message: str = "") -> None:
        super().__init__(bot, tag="unsupported")
        self.message = message


#############################################################################################################################
# COG
#############################################################################################################################


class FriendsCog(commands.Cog[Dota2Bot]):
    """Component with all 9kmmrbot-like Dota 2 features.

    This component uses Dota 2 Rich Presence (RP), Dota 2 web API and Dota 2 Game Coordinator (GC) API calls
    to figure out current state of each friend in the bot's steam friend list.
    If they are currently playing - the bot activates special chat commands inspecting their live match.
    The bot also stores some match history information in the database for some features/commands to use.

    Notes
    -----
    * The restrictions of such approach are
            1. Streamers have to add the bot into their friend list;
            2. Streamers have to play Dota 2 while being green-online in steam;
        The other solution that would solve these problems would be using Dota 2 Game State Integration (GSI),
        which I'm going to implement one day.
    """

    def __init__(self) -> None:
        super().__init__()

        self.streamer_index_ready: asyncio.Event = asyncio.Event()

        self.fill_streamers.start()
        self.add_on_user_update_listener.start()
        self.double_check_gc_match_history.start()
        self.remove_way_too_old_matches.start()
        self.tuesday_problem.start()

    @override
    async def cog_unload(self) -> None:
        """Cog Unload."""
        self.fill_streamers.cancel()
        self.add_on_user_update_listener.cancel()
        self.double_check_gc_match_history.cancel()
        self.remove_way_too_old_matches.cancel()
        self.tuesday_problem.cancel()

    @tasks.loop(count=1)
    async def fill_streamers(self) -> None:
        """Index bot's friend list on bot's startup.

        Also makes initial analyse of their rich presences.
        """
        log.debug("Indexing bot's friend list.")
        for friend in await self.bot.user.friends():
            # we need to use `friend._user` because `Friend` class doesn't have Dota 2 features which User class have.
            self.bot.friends_index[friend.id] = Friend(self.bot, friend._user)  # ruff: ignore[private-member-access]  # ty: ignore[invalid-argument-type]

        for streamer in self.bot.friends_index.values():
            await self.analyze_rich_presence(streamer)

        log.debug('Friends index ready. Setting "_friends_index_ready".')
        self.streamer_index_ready.set()

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_user_update(self, before: dota2.User, after: dota2.User) -> None:
        """Event when a steam user is updated, due to one or more of their attributes changing."""
        rp_before = RichPresence(raw=before.rich_presence)
        rp_after = RichPresence(raw=after.rich_presence)

        if rp_before == rp_after:
            return

        streamer = self.bot.friends_index.setdefault(after.id, Friend(self.bot, after))
        streamer.rich_presence = rp_after
        log.debug("Recognized rich presence update for %r: %r", streamer, streamer.rich_presence)
        await self.analyze_rich_presence(streamer)

    async def analyze_rich_presence(self, streamer: Friend) -> None:
        """Analyze Rich Presence.

        Central function for this component.
        The bot tracks streamers's game-flow via inspecting their rich presence.
        If the bot detects a new game being played or watched - it will do necessary actions to
        prepare those matches for further inspection.

        Warning
        -------
        The code in this function is also quite volatile. Valve are quite inconsistent in their Rich Presence data,
        so the logic might break any day.
        """
        if streamer.is_playing_dota():
            # Update `last_seen` in the database;
            query = "UPDATE ttv_dota_accounts SET last_seen = $1 WHERE friend_id = $2;"
            await self.bot.pool.execute(query, clock.utcnow(), streamer.steam.id)
        else:
            # not interested if not playing Dota 2
            streamer.activity = Incomplete("Not Green In Dota")
            await self.conclude_match(streamer)
            return

        if not await streamer.update_activity():
            # No new activity was detected
            return

        match streamer.activity:
            case Dashboard():
                await self.conclude_match(streamer)

            case PlayingPartialMatch():
                watchable_game_id = streamer.activity.watchable_game_id
                if watchable_game_id not in self.bot.play_matches:
                    self.bot.play_matches[watchable_game_id] = PlayingMatch(self.bot, watchable_game_id)
                streamer.live_match = self.bot.play_matches[watchable_game_id]

            case SpectatingPartialMatch():
                watching_server = streamer.activity.watching_server
                if watching_server not in self.bot.spectate_matches:
                    self.bot.spectate_matches[watching_server] = SpectatingMatch(self.bot, watching_server)
                streamer.live_match = self.bot.spectate_matches[watching_server]

            case UnsupportedPartialMatch():
                streamer.live_match = UnsupportedMatch(self.bot, message=streamer.activity.msg)

            case _:
                # Incomplete activities - wait for confirmed activities.
                # Do nothing.
                return

    #########################################################################################################################
    # PENDING MATCHES
    #########################################################################################################################

    async def conclude_match(self, streamer: Friend) -> None:
        """Conclude match as finished.

        This nulls `Friend.live_match` attribute as well as adds the match into the database
        if it's a `PlayingMatch`.
        """
        match: LiveMatch | None = streamer.live_match
        if isinstance(match, PlayingMatch) and bool(match.id):
            if match.state == MatchState.Live:
                match.state = MatchState.Pending
                query = "UPDATE ttv_dota_matches SET live = $1 WHERE match_id = $2;"
                await self.bot.pool.execute(query, MatchState.Pending, match.id)
                if not self.process_pending_matches.is_running():
                    self.process_pending_matches.start()
            elif match.state == MatchState.Starting:
                # It means that the lobby terminated before heroes were picked;
                match.update_data.cancel()

        streamer.live_match = None

    @tasks.loop(seconds=20)
    async def process_pending_matches(self) -> None:
        """Process pending matches.

        Development Notes
        -----------------
        * We use match history endpoint specifically because it has `.abandon` attribute.
            So if we remove that quirk - we can probably be fine with just "minimal_match" data.
        * Another option is to use opendota api (stratz is too slow -
            they don't allow access to data until full parse is done)
        """
        log.debug("Processing pending matches.")

        query = "SELECT match_id, failed FROM ttv_dota_matches WHERE outcome IS NULL AND live = $1 AND failed < 17;"
        rows = await self.bot.pool.fetch(query, MatchState.Pending)
        if not rows:
            # I guess no pending matches left
            self.process_pending_matches.cancel()
            return

        for row in rows:
            try:
                # If streamer disconnects before ancient falls (e.g., preemptive disconnects or when game is "Safe to leave")
                # Then `.minimal` won't give any results as the game is still live but streamer's RP is different
                # So we need to deal with errors of not getting response from it.
                # This also happens if streamer disconnects-reconnects in the middle of the match.
                minimal = await self.bot.create_partial_match(row["match_id"]).minimal()
            except ValueError:
                # this way any matches that errored out more 12 times gonna be ignored
                # not sure how I feel about such solution;
                query = "UPDATE ttv_dota_matches SET failed = failed + 1 WHERE match_id = $1;"
                await self.bot.pool.execute(query, row["match_id"])
                continue

            query = "UPDATE ttv_dota_matches SET outcome = $1, live = $3 WHERE match_id = $2;"
            await self.bot.pool.execute(query, minimal.outcome, row["match_id"], MatchState.Completed)

    #########################################################################################################################
    # MATCH HISTORY AND MATCHES DATABASE CARE
    #########################################################################################################################

    @tasks.loop(hours=1)
    async def double_check_gc_match_history(self) -> None:
        """Double check match history in case missed some games.

        Useful to keep W-L as precise as possible.
        """
        for streamer in self.bot.friends_index.values():
            for match in await streamer.steam.match_history():
                await self.add_match_history_match_to_database(match, streamer.steam.id)

    async def add_match_history_match_to_database(self, match: dota2.MatchHistoryMatch, friend_id: int) -> None:
        """Add matches from match history check loop into the database."""
        if match.start_time < clock.utcnow() - dt.timedelta(hours=48):
            # Match is way too old to care
            return

        # match history entities on their own do not give proper outcome (Radiant/Dire)
        minimal = await self.bot.create_partial_match(match.id).minimal()

        query = """
            INSERT INTO ttv_dota_matches
                (match_id, start_time, lobby_type, game_mode, outcome, live)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (match_id) DO
                UPDATE SET live = $6;
        """
        await self.bot.pool.execute(
            query,
            match.id,
            match.start_time,
            match.lobby_type,
            match.game_mode,
            minimal.outcome,
            MatchState.Completed,
        )
        player_slot = next((slot for slot, player in enumerate(minimal.players) if player.hero == match.hero), None)
        assert player_slot is not None, "Somehow `player_slot` is `None` in match history match"

        query = """
            INSERT INTO ttv_dota_match_players
            (friend_id, match_id, hero_id, player_slot, abandon)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (friend_id, match_id) DO
                UPDATE SET abandon = $5;
        """
        await self.bot.pool.execute(query, friend_id, match.id, match.hero.id, player_slot, match.abandon)

    @tasks.loop(hours=6)
    async def remove_way_too_old_matches(self) -> None:
        """Clean the database from way too old matches.

        Currently, 48 hours is considered as "too old".
        """
        # if self.remove_way_too_old_matches.current_loop == 0:
        #     # No need to bother on bot reloads.
        #     return

        log.debug("Removing way too old matches from the database.")
        query = "DELETE FROM ttv_dota_matches WHERE start_time < $1;"
        await self.bot.pool.execute(query, clock.utcnow() - dt.timedelta(hours=48))

        cut_off_dt = clock.utcnow() - dt.timedelta(hours=8)

        def remove_from_index(index: dict[str, Any]) -> None:
            for match_id in list(index.keys()):
                match = index[match_id]
                if match.started_at < cut_off_dt:
                    index.pop(match_id)

        remove_from_index(self.bot.play_matches)
        remove_from_index(self.bot.spectate_matches)

    #########################################################################################################################
    # TASK CARE
    #########################################################################################################################

    @tasks.loop(count=1)
    async def add_on_user_update_listener(self) -> None:
        """Add `on_user_update` listener after all the clients are ready."""
        self.bot.add_listener(self.on_user_update, name="on_user_update")

    @add_on_user_update_listener.before_loop
    @fill_streamers.before_loop
    async def wait_for_clients(self) -> None:
        """Wait for all the needed clients to get ready.

        Otherwise, such things as Dota 2 Coordinator requests are going to error out.
        Unfortunately, specifically, Dota 2 Coordinator usually takes ~30 seconds to get ready.
        """
        await self.bot.wait_until_ready()
        await self.bot.wait_until_gc_ready()

    @double_check_gc_match_history.before_loop
    @remove_way_too_old_matches.before_loop
    async def wait_for_streamer_index(self) -> None:
        """Extra waiting for streamer index to get filled with initial data.

        This calls `.wait_for_clients` which also waits for all the required clients to be ready.
        """
        await self.wait_for_clients()
        await self.streamer_index_ready.wait()

    @tasks.loop(count=1)
    async def tuesday_problem(self) -> None:
        """Crutch to try to solve infamous Tuesday Steam maintenance problems.

        When steam crashes - it is always a big problem for the bot;
        It doesn't ever properly reconnect or restart on its own.

        Let's put a crutch to simply restart the bot if that happens.
        """
        try:
            async with asyncio.timeout(11 * 60):  # 11 minutes
                await self.bot.wait_until_gc_ready()
        except TimeoutError:
            log.critical("🔴 Failed to wait for Dota 2 Game Coordinator to get ready - restarting the bot. 🔴")
            try:
                await asyncio.create_subprocess_shell("sudo systemctl restart dota2bot")
            except Exception:
                log.exception("Failed to Restart the bot's process", stack_info=True)


async def setup(bot: Dota2Bot) -> None:
    """Load Dota2Bot's extension. Framework of steamio."""
    await bot.add_cog(FriendsCog())
