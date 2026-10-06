from __future__ import annotations

import asyncio
import datetime
import functools
import itertools
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict, TypeVar, override

import steam
from discord.utils import MISSING
from steam.ext import dota2

from core import ireloop
from shared import dota2 as dota2utils, errors

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from core import IreBot

    type ActiveMatch = PlayingMatch | SpectatingMatch | UnsupportedActivity

    class ScoreQueryRow(TypedDict):
        friend_id: int
        start_time: datetime.datetime
        lobby_type: int
        game_mode: int
        outcome: int | None
        player_slot: int
        abandon: bool

    class NotablePlayersQueryRow(TypedDict):
        friend_id: int
        nickname: str

    class PendingAbandonsQueryRow(TypedDict):
        friend_id: int
        match_id: int
        outcome: int
        lobby_type: int
        player_slot: int


LM = TypeVar("LM", bound="LiveMatch")


log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

NKMMRBOT_MODULE_NAME = __name__


@dataclass
class Score:
    wins: int
    losses: int
    abandons: int
    pending: int


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
    async def create(cls, bot: IreBot, account_id: int, player_slot: int) -> Player:
        partial_user = bot.dota2.create_partial_user(account_id)
        profile_card = await partial_user.dota2_profile_card()

        return Player(
            friend_id=account_id,
            player_slot=player_slot,
            lifetime_games=profile_card.lifetime_games,
            medal=dota2utils.rank_medal_display_name(profile_card),
        )

    @property
    def color(self) -> str:
        colors = ["Blue", "Teal", "Purple", "Yellow", "Orange", "Pink", "Olive", "LightBlue", "DarkGreen", "Brown"]
        try:
            return colors[self.player_slot]
        except IndexError:
            return "Colorless"


def format_match_response(func: Callable[..., Coroutine[Any, Any, str]]) -> Callable[..., Coroutine[Any, Any, str]]:
    @functools.wraps(func)
    async def wrapper(self: LiveMatch, *args: Any, **kwargs: Any) -> str:
        if isinstance(self, UnsupportedActivity):
            return self.message

        prefix = f"[{self.activity_tag}] " if self.activity_tag else ""
        if isinstance(self, SpectatingMatch) and self.unavailable:
            response = "I'm not able to fetch data for this match, sorry."
        else:
            response = await func(self, *args, **kwargs)
        return prefix + response

    return wrapper


class LiveMatch:
    def __init__(self, bot: IreBot, tag: str = "") -> None:
        self.bot: IreBot = bot
        self.activity_tag: str = tag

        # match data
        self.match_id: int | None = None
        self.lobby_type: dota2.LobbyType | None = None
        self.game_mode: dota2.GameMode | None = None
        self.server_steam_id: int = MISSING

        # players
        self.players: list[Player] = []
        self.heroes: list[dota2.Hero] = []

        # ready events
        self.players_data_ready: asyncio.Event = asyncio.Event()
        self.heroes_data_ready: asyncio.Event = asyncio.Event()

        # friends
        self.friends: set[Friend] = set()

        self.started_at: datetime.datetime = datetime.datetime.now(datetime.UTC)

    def _is_players_data_ready(self) -> bool:
        """A condition to check whether match player data is filled properly."""
        return bool(self.game_mode) and all(bool(player) for player in self.players)

    def _is_heroes_data_ready(self) -> bool:
        """A condition to check whether match hero data is filled properly."""
        return bool(self.heroes) and all(bool(hero) for hero in self.heroes)

    @format_match_response
    async def lead(self) -> str:
        """Response for !lead command."""
        if not self.server_steam_id:
            return "This match doesn't support real time stats"
        match = await self.bot.dota2.web_api.get_real_time_stats(self.server_steam_id)
        radiant = match["teams"][0]
        dire = match["teams"][1]

        lead = radiant["net_worth"] - dire["net_worth"]
        word = "Radiant" if lead > 0 else "Dire"

        return f"[2m delay] Radiant {radiant['score']} - Dire {dire['score']}: {word} is leading by {abs(lead) / 1000:.1f}k"

    @format_match_response
    async def notable_players(self) -> str:
        """Response for !notable command."""
        if not self.players:
            return "No player data yet."

        query = """
            SELECT friend_id, nickname
            FROM ttv_dota_notable_players
            WHERE friend_id = ANY($1);
        """
        rows: list[NotablePlayersQueryRow] = await self.bot.pool.fetch(query, [p.friend_id for p in self.players])
        if not rows:
            return "No notable players found"

        nickname_mapping = {row["friend_id"]: row["nickname"] for row in rows}

        response_parts = [
            f"{nick} as {hero or player.color}"
            for player, hero in zip(self.players, self.heroes, strict=True)
            if (nick := nickname_mapping.get(player.friend_id))
        ]
        return " \N{BULLET} ".join(response_parts)

    @format_match_response
    async def server_steam_id_command_response(self) -> str:
        """Command response for !server_steam_id."""
        return str(self.server_steam_id)


class PlayingMatch(LiveMatch):
    def __init__(self, bot: IreBot, watchable_game_id: str) -> None:
        super().__init__(bot)
        self.watchable_game_id: str = watchable_game_id
        self.lobby_id: int = int(watchable_game_id)

        self.average_mmr: int | None = None
        self.live: dota2utils.LiveIndicator = dota2utils.LiveIndicator.Starting

        self.update_data.start()

    @override
    def __hash__(self) -> int:
        return self.match_id or super().__hash__()

    @ireloop(seconds=10.1, count=30)
    async def update_data(self) -> None:
        log.debug('Updating %s data for watchable_game_id "%s"', self.__class__.__name__, self.watchable_game_id)
        match = next(iter(await self.bot.dota2.live_matches(lobby_ids=[self.lobby_id])), None)
        if not match:
            msg = f'FindTopSourceTVGames did not find watchable_game_id "{self.watchable_game_id}".'
            raise errors.SomethingWentWrongError(msg)

        if not self.players_data_ready.is_set():
            # match data
            self.server_steam_id = match.server_steam_id
            self.match_id = match.id
            self.average_mmr = match.average_mmr
            self.lobby_type = match.lobby_type
            self.game_mode = match.game_mode
            self.started_at = match.start_time

            # players
            self.players = [
                await Player.create(self.bot, gc_player.id, player_slot)
                for player_slot, gc_player in enumerate(match.players)
            ]
            if self._is_players_data_ready():
                self.players_data_ready.set()
                log.debug('%s players data ready for: "%s"', self.__class__.__name__, self.watchable_game_id)
                self.bot.dispatch("players_data_ready", self)

        if not self.heroes_data_ready.is_set():
            self.heroes = [gc_player.hero for gc_player in match.players]
            if self._is_heroes_data_ready():
                self.heroes_data_ready.set()
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watchable_game_id)
                self.bot.dispatch("heroes_data_ready", self)

        if self.players_data_ready.is_set() and self.heroes_data_ready.is_set():
            # add to the database
            if self.lobby_type == dota2.LobbyType.Practice:
                # These lobby types do not leave any trace for match history purposes
                # I.e. after playing in a practice lobby - there is
                # no match to inspect in match history, opendota, etc;
                # And `match.minimal()` errors out with `ValueError`

                self.live = dota2utils.LiveIndicator.Live

                query = """
                    INSERT INTO ttv_dota_matches
                    (match_id, start_time, lobby_type, game_mode)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (match_id) DO NOTHING;
                """
                await self.bot.pool.execute(query, self.match_id, match.start_time, self.lobby_type, self.game_mode)

                for friend in self.friends:
                    if friend.rich_presence.status == dota2utils.Status.Coaching:
                        # If the person is coaching then the match will not be in their match history -
                        # we don't need to track WinLoss or etc ; no need to add it into the database ;
                        continue

                    player_slot = next(iter(s for (s, p) in enumerate(match.players) if p.id == friend.steam_user.id), None)
                    if player_slot is None:
                        msg = "Somehow 'player_slot' is 'None' in 'conclude_friend_match'"
                        raise errors.SomethingWentWrongError(msg)

                    hero = match.heroes[player_slot]
                    query = """
                        INSERT INTO ttv_dota_match_players
                        (friend_id, match_id, hero_id, player_slot)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (friend_id, match_id) DO NOTHING;
                    """
                    await self.bot.pool.execute(query, friend.steam_user.id, self.match_id, hero.id, player_slot)

            self.update_data.stop()

    @override
    async def game_medals(self) -> str:
        mmr_notice = f"[{self.average_mmr} avg] " if self.average_mmr else ""
        return mmr_notice + await super().game_medals()

    async def played_with(self, friend_id: int, last_game: dota2.MinimalMatch) -> str:
        if not self.players:
            return "No player data yet."

        last_game_hero_player_index: dict[int, dota2.Hero] = {p.id: p.hero for p in last_game.players}
        last_game_hero_player_index.pop(friend_id, None)  # remove the streamer themselves

        response_parts = [
            f"{hero or player.color} played as {last_game_played_as}"
            for player, hero in zip(self.players, self.heroes, strict=True)
            if (last_game_played_as := last_game_hero_player_index.get(player.friend_id))
        ]
        if response_parts:
            return " \N{BULLET} ".join(response_parts)
        return "No players from the last game present in the match"


class SpectatingMatch(LiveMatch):
    def __init__(self, bot: IreBot, watching_server: str) -> None:
        super().__init__(bot, tag="Spectating")
        self.watching_server: str = watching_server
        # Steam Web API uses Steam IDs for servers in id64 format, but Rich Presence provides them in id3.
        # Example: id3: [A:1:3513917470:30261] -> id64: 90201966066671646.
        steam_id = steam.ID.from_id3(watching_server)
        if steam_id is None:
            msg = "Failed to get steam ID from id3."
            raise errors.SomethingWentWrongError(msg)
        self.server_steam_id: int = steam_id.id64
        self.unavailable: bool = False

        self.update_data.start()

    @ireloop(seconds=10.1, count=30)
    async def update_data(self) -> None:
        log.debug('Updating %s data for watching_server "%s"', self.__class__.__name__, self.watching_server)
        try:
            match = await self.bot.dota2.web_api.get_real_time_stats(self.server_steam_id)
        except errors.APIDataError:
            # If SteamWebAPI didn't respond with any data then we have no way to get the data
            self.unavailable = True
            self.update_data.stop()
            return

        api_players = list(itertools.chain(match["teams"][0]["players"], match["teams"][1]["players"]))

        if not self.players_data_ready.is_set():
            # match data
            self.match_id = int(match["match"]["match_id"])
            self.lobby_type = dota2.LobbyType.try_value(match["match"]["lobby_type"])
            self.game_mode = dota2.GameMode.try_value(match["match"]["game_mode"])
            self.started_at = datetime.datetime.fromtimestamp(match["match"]["start_timestamp"], tz=datetime.UTC)

            # players
            self.players = [
                await Player.create(self.bot, api_player["accountid"], player_slot)
                for player_slot, api_player in enumerate(api_players)
            ]
            if self._is_players_data_ready():
                self.players_data_ready.set()
                log.debug('%s players data ready for: "%s"', self.__class__.__name__, self.watching_server)
                self.bot.dispatch("players_data_ready", self)

        if not self.heroes_data_ready.is_set():
            self.heroes = [dota2.Hero.try_value(api_player["heroid"]) for api_player in api_players]
            if self._is_heroes_data_ready():
                self.heroes_data_ready.set()
                log.debug('%s heroes data ready for: "%s"', self.__class__.__name__, self.watching_server)
                self.bot.dispatch("heroes_data_ready", self)

        if self.players_data_ready.is_set() and self.heroes_data_ready.is_set():
            self.update_data.stop()


class UnsupportedActivity(LiveMatch):
    """A class describing unsupported matches.

    All chat commands for objects of this type should return unsupported message response.
    For example, if streamer is playing Demo Mode, then the bot should only respond with "Demo Mode is not supported",
    because, well, there is no data in Demo Mode to insect.
    """

    def __init__(self, bot: IreBot, message: str = "") -> None:
        super().__init__(bot, "")
        self.message = message
