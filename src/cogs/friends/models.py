from __future__ import annotations

import asyncio
import datetime as dt
import itertools
import logging
import pprint
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, override

import steam
from steam.ext import dota2

from shared import clock, errors
from shared.concepts import tasks

from . import activities, enums
from .tools import rank_medal_display_name

if TYPE_CHECKING:
    from core import Dota2Bot

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


class RichPresence:
    """Rich Presence.

    Normally Rich Presence is just a dictionary of data.
    This class adds some utility for GameFlow component to use.
    """

    def __init__(self, raw: dict[str, str] | None) -> None:
        self.raw: dict[str, str] = raw or {}
        self.status: enums.Status = (
            enums.Status.try_value(value=raw.get("status", "#MY_NO_STATUS")) if raw else enums.Status.RichPresenceNone
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


class Streamer:
    """Streamer.

    Name note
    ---------
    It would make a lot more sense to name this class "Friend", but I'm afraid I might confuse it for steamio's Friend, so
    I guess "Streamer" is fine.
    """

    def __init__(self, bot: Dota2Bot, steam: dota2.User) -> None:
        self._bot: Dota2Bot = bot
        self.steam: dota2.User = steam

        # Activity related attributes
        # later they all gets updated with `on_user_update` steamio event
        self.rich_presence: RichPresence = RichPresence(steam.rich_presence)
        self.activity: activities.Activity = activities.Incomplete(
            "The bot has not received any Rich Presence information yet"
        )
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

    async def get_activity(self) -> activities.Activity:
        """Get a new value for `self.activity`."""

        rp = self.rich_presence

        # Dashboard
        if rp.status in {
            enums.Status.Idle,
            enums.Status.MainMenu,
            enums.Status.Finding,
        }:
            return activities.Dashboard()

        # Playing
        if rp.status in {
            enums.Status.WaitingToLoad,
            enums.Status.HeroSelection,
            enums.Status.Strategy,
            enums.Status.PreGame,
            enums.Status.WaitingForMapToLoad,
            enums.Status.Playing,
            enums.Status.Coaching,
        }:
            watchable_game_id = rp.raw.get("WatchableGameID")
            if watchable_game_id is None:
                # something is off
                lobby_param0 = rp.raw.get("param0", "_missing")
                return {
                    enums.LobbyParam0.DemoMode: activities.UnsupportedPartialMatch("Demo mode is not supported"),
                    enums.LobbyParam0.BotMatch: activities.UnsupportedPartialMatch("Bot matches are not supported"),
                }.get(
                    lobby_param0,
                    activities.Incomplete("Unknown (Rich Presence is 'playing' but watchable_game_id is None)"),
                )
            if watchable_game_id == "0":
                # something is off again
                # usually this happens when a player has just quit the match into the main menu
                # the status flickers for a few seconds to be `watchable_game_id=0`
                return activities.Incomplete("Unknown (Rich Presence is 'playing' but watchable_game_id=0)")
            return activities.PlayingPartialMatch(watchable_game_id)

        # Watching
        if rp.status in {
            enums.Status.Spectating,
            enums.Status.WatchingTournament,
            enums.Status.WatchingTI,
        }:
            watching_server = rp.raw.get("watching_server")
            if watching_server is None:
                # something is off, but I think this happens only for watching replays
                return activities.UnsupportedPartialMatch("Watching replays is not supported")
            return activities.SpectatingPartialMatch(watching_server)

        # Closed Dota?
        if rp.status == enums.Status.NoStatus:
            # usually this happens in exact moment when the player closes Dota
            return activities.Incomplete("Closed Dota")

        # Other statuses
        other_statuses = {
            enums.Status.BotPractice: "Demo mode is not supported",
            enums.Status.PrivateLobby: "Bot matches are not supported",
            enums.Status.CustomGameProgress: "Custom games are not supported",
            enums.Status.CustomGameLobby: "Private lobbies (this includes draft in public lobbies) are not supported",
            enums.Status.Crownfall: "Crownfall activities are not supported.",
            enums.Status.DarkCarnival: "Dark Carnival activities are not supported.",
            enums.Status.CoopBot: "Coop vs Bots matches are not supported",
        }
        if msg := other_statuses.get(rp.status):
            return activities.UnsupportedPartialMatch(msg)

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
        await self._bot.ping_developers(text)

        return activities.Incomplete("Unknown ('_get_activity' got lost)")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.steam.id,
            "name": self.steam.name,
            "is_playing_dota": self.is_playing_dota(),
            "status": self.rich_presence.status.display_name,
            "rich_presence": str(self.rich_presence),
            "raw_rich_presence": self.rich_presence.raw,
            "activity": str(self.activity),
            "live_match": self.live_match.to_dict() if self.live_match else None,
        }

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

    friend_id: int
    player_slot: int
    lifetime_games: int
    medal: str

    @override
    def __repr__(self) -> str:
        return f"<Player id={self.friend_id} slot={self.color}>"

    def __bool__(self) -> bool:
        return bool(self.friend_id)

    @classmethod
    async def create(cls, bot: Dota2Bot, account_id: int, player_slot: int) -> Player:
        partial_user = bot.create_partial_user(account_id)
        profile_card = await partial_user.dota2_profile_card()

        return Player(
            friend_id=account_id,
            player_slot=player_slot,
            lifetime_games=profile_card.lifetime_games,
            medal=rank_medal_display_name(profile_card),
        )

    @property
    def color(self) -> str:
        colors = ["Blue", "Teal", "Purple", "Yellow", "Orange", "Pink", "Olive", "LightBlue", "DarkGreen", "Brown"]
        try:
            return colors[self.player_slot]
        except IndexError:
            return "Colorless"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.friend_id,
            "player_slot": self.player_slot,
            "color": self.color,
            "lifetime_games": self.lifetime_games,
            "medal": self.medal,
        }


class LiveMatch:
    def __init__(self, bot: Dota2Bot, tag: str, message: str = "") -> None:
        self.bot: Dota2Bot = bot
        self.tag: str = tag
        self.message: str = message

        # Common match data
        self.match_id: int | None = None
        self.lobby_type: dota2.LobbyType | None = None
        self.game_mode: dota2.GameMode | None = None
        self.server_steam_id: int | None = None

        # players
        self.players: list[Player] = []
        self.heroes: list[dota2.Hero] = []

        # ready events
        self.players_data_ready: asyncio.Event = asyncio.Event()
        self.heroes_data_ready: asyncio.Event = asyncio.Event()
        self.ready: bool = False

        # other
        self.streamers: set[Streamer] = set()
        self.started_at: dt.datetime = clock.utcnow()
        self.average_mmr: int | None = None
        self.unavailable: bool = False

    def _is_players_data_ready(self) -> bool:
        """A condition to check whether match player data is filled properly."""
        return bool(self.game_mode) and all(bool(player) for player in self.players)

    def _is_heroes_data_ready(self) -> bool:
        """A condition to check whether match hero data is filled properly."""
        return bool(self.heroes) and all(bool(hero) for hero in self.heroes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "message": self.message,
            "ready": self.ready,
            "match_id": self.match_id,
            "lobby_type": self.lobby_type,
            "lobby_type_name": self.lobby_type.display_name if self.lobby_type else "",
            "game_mode": self.game_mode,
            "game_mode_name": self.game_mode.display_name if self.game_mode else "",
            "server_steam_id": self.server_steam_id,
            "players": [
                player.to_dict()
                | {
                    "hero_id": hero.id,
                    "hero_name": hero.name if hero else "",
                }
                for player, hero in zip(self.players, self.heroes, strict=True)
            ],
            "started_at": self.started_at,
            "average_mmr": self.average_mmr,
            "unavailable": self.unavailable,
        }


class SpectatingMatch(LiveMatch):
    def __init__(self, bot: Dota2Bot, watching_server: str) -> None:
        super().__init__(bot, tag="spectating")
        self.watching_server: str = watching_server
        # Steam Web API uses Steam IDs for servers in id64 format, but Rich Presence provides them in id3.
        # Example: id3: [A:1:3513917470:30261] -> id64: 90201966066671646.
        steam_id = steam.ID.from_id3(watching_server)
        assert steam_id, "Failed to get Steam ID from id3."
        self.server_steam_id: int = steam_id.id64
        self.update_data.start()

    @tasks.loop(seconds=10.1, count=30)
    async def update_data(self) -> None:
        log.debug('Updating %s data for watching_server "%s"', self.__class__.__name__, self.watching_server)
        try:
            match = await self.bot.web_api.get_real_time_stats(self.server_steam_id)
        except errors.ApiError:
            # If SteamWebAPI didn't respond with any data then we have no way to get the data
            self.unavailable = True
            self.update_data.stop()
            return

        api_players = list(itertools.chain(match["teams"][0]["players"], match["teams"][1]["players"]))

        if not self.players_data_ready.is_set():
            self.match_id = int(match["match"]["match_id"])
            self.lobby_type = dota2.LobbyType.try_value(match["match"]["lobby_type"])
            self.game_mode = dota2.GameMode.try_value(match["match"]["game_mode"])
            self.started_at = dt.datetime.fromtimestamp(match["match"]["start_timestamp"], tz=dt.UTC)
            self.players = [
                await Player.create(self.bot, api_player["accountid"], player_slot)
                for player_slot, api_player in enumerate(api_players)
            ]
            if self._is_players_data_ready():
                self.players_data_ready.set()
                log.debug('%s players data ready for: "%s"', self.__class__.__name__, self.watching_server)
                # self.bot.dispatch("players_data_ready", self)

        if not self.heroes_data_ready.is_set():
            self.heroes = [dota2.Hero.try_value(api_player["heroid"]) for api_player in api_players]
            if self._is_heroes_data_ready():
                self.heroes_data_ready.set()
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watching_server)
                # self.bot.dispatch("heroes_data_ready", self)

        if self.players_data_ready.is_set() and self.heroes_data_ready.is_set():
            self.ready = True
            self.update_data.stop()


class PlayingMatch(LiveMatch):
    def __init__(self, bot: Dota2Bot, watchable_game_id: str) -> None:
        super().__init__(bot, "playing")
        self.watchable_game_id: str = watchable_game_id
        self.lobby_id: int = int(watchable_game_id)

        self.average_mmr: int | None = None
        self.state: enums.PlayingMatchState = enums.PlayingMatchState.Starting

        self.update_data.start()

    @tasks.loop(seconds=10.1, count=30)
    async def update_data(self) -> None:
        log.debug('Updating %s data for watchable_game_id "%s"', self.__class__.__name__, self.watchable_game_id)
        match = next(iter(await self.bot.live_matches(lobby_ids=[self.lobby_id])), None)
        assert match, f'FindTopSourceTVGames did not find watchable_game_id "{self.watchable_game_id}".'

        if not self.players_data_ready.is_set():
            self.server_steam_id = match.server_steam_id
            self.match_id = match.id
            self.average_mmr = match.average_mmr
            self.lobby_type = match.lobby_type
            self.game_mode = match.game_mode
            self.started_at = match.start_time

            self.players = [
                await Player.create(self.bot, gc_player.id, player_slot)
                for player_slot, gc_player in enumerate(match.players)
            ]
            if self._is_players_data_ready():
                self.players_data_ready.set()
                log.debug('%s players data ready for: "%s"', self.__class__.__name__, self.watchable_game_id)
                # self.bot.dispatch("players_data_ready", self)

        if not self.heroes_data_ready.is_set():
            self.heroes = [gc_player.hero for gc_player in match.players]
            if self._is_heroes_data_ready():
                self.heroes_data_ready.set()
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watchable_game_id)
                # self.bot.dispatch("heroes_data_ready", self)

        if self.players_data_ready.is_set() and self.heroes_data_ready.is_set():
            # add to the database
            if self.lobby_type == dota2.LobbyType.Practice:
                # These lobby types do not leave any trace for match history purposes
                # I.e. after playing in a practice lobby - there is
                # no match to inspect in match history, opendota, etc;
                # And `match.minimal()` errors out with `ValueError`

                self.state = enums.PlayingMatchState.Live

                query = """
                    INSERT INTO ttv_dota_matches
                    (match_id, start_time, lobby_type, game_mode)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (match_id) DO NOTHING;
                """
                await self.bot.pool.execute(query, self.match_id, match.start_time, self.lobby_type, self.game_mode)

                for streamer in self.streamers:
                    if streamer.rich_presence.status == enums.Status.Coaching:
                        # If the person is coaching then the match will not be in their match history -
                        # we don't need to track WinLoss or etc ; no need to add it into the database ;
                        continue

                    player_slot = next(iter(s for (s, p) in enumerate(match.players) if p.id == streamer.steam.id), None)
                    assert player_slot, "Somehow 'player_slot' is 'None' in 'conclude_friend_match'"

                    hero = match.heroes[player_slot]
                    query = """
                        INSERT INTO ttv_dota_match_players
                        (friend_id, match_id, hero_id, player_slot)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (friend_id, match_id) DO NOTHING;
                    """
                    await self.bot.pool.execute(query, streamer.steam.id, self.match_id, hero.id, player_slot)

            self.ready = True
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
