# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Matches.

What the friends are doing in Dota 2 matches and the code that collects the data about those matches.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import steam
from steam.ext import dota2

from shared import clock
from shared.apis.local import MatchState

from . import queries
from .models import Player

if TYPE_CHECKING:
    import datetime as dt
    from collections.abc import Callable

    from steam.ext.dota2 import Hero

    from core import Dota2Bot
    from shared.typings import local as schemas

    from .models import Friend

__all__ = (
    "LiveMatch",
    "PlayingMatch",
    "SpectatingMatch",
    "TrackedMatch",
    "UnsupportedMatch",
    "get_or_create",
)

log = logging.getLogger(__name__)

POLL_INTERVAL = 10.1  # seconds between attempts to get live match data
POLL_ATTEMPTS = 30  # attempts before giving up on a live match
# Steam gives these ids to players with hidden profiles, they can't identify a player in the database.
ANONYMOUS_ACCOUNT_IDS = frozenset({0, 4294967295})


class LiveMatch:
    """Base class for whatever a friend is currently doing in a match.

    Attributes
    ----------
    state
        Where the match is in its life cycle, see ``MatchState``:

        * ``Starting`` - the match data is being collected, i.e. players and heroes are not all known yet.
        * ``Live`` - the data is complete (and saved, for playing matches). The match is still going.
        * ``Pending`` - the last friend has left a live match, so the bot is waiting for its outcome.
        * ``Completed`` - the outcome is known. Set by the cog after it has looked the outcome up.
        * ``GaveUp`` - the bot failed to get the match data or the lobby ended before heroes were picked.
    ready
        Set once the players and their heroes are known (and the match data is saved).
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

    def add_friend(self, friend: Friend) -> None:
        """Register a friend as a participant of this match.

        Parameters
        ----------
        friend
            The friend who takes part in the match.
        """
        self.friends.add(friend)


class UnsupportedMatch(LiveMatch):
    """A match the bot can't inspect.

    All chat commands for objects of this type should respond with the message.
    For example, if a friend is playing Demo Mode, then the bot should only respond with "Demo Mode is not supported",
    because, well, there is no data in Demo Mode to inspect.

    Parameters
    ----------
    bot
        The bot.
    message
        Explanation of why the match is not supported.
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
        ``(account id, hero)`` pairs in player slot order.
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

    Subclasses implement ``fetch_snapshot`` and call ``start`` at the end of their ``__init__``.
    """

    def __init__(self, bot: Dota2Bot, tag: schemas.TagLiteral) -> None:
        super().__init__(bot, tag)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start polling for the match data."""
        self._task = asyncio.create_task(self._poll())

    def cancel(self) -> None:
        """Stop polling and mark the match as abandoned by the bot."""
        if self._task is not None:
            self._task.cancel()
        self.state = MatchState.GaveUp

    @abstractmethod
    async def fetch_snapshot(self) -> MatchSnapshot:
        """Fetch the current match data.

        Raises
        ------
        Exception
            Any exception, if the data is not available (yet). The attempt is repeated later.
        """

    async def on_live(self) -> None:
        """Hook called once, when the match data is complete.

        The match is already marked as live at this point, so it can't get cancelled while the hook is working.
        An exception raised here is logged and doesn't stop the match from being ready.
        """

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
                self.state = MatchState.Live
                try:
                    await self.on_live()
                except Exception:
                    log.exception("%r: on_live failed", self)
                self.ready.set()
                log.info("%r is live with %d players", self, len(self.players))
                return

        log.warning("%r: gave up getting the match data after %d attempts", self, POLL_ATTEMPTS)
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
        if steam_id is None:
            msg = f"Failed to get Steam ID from id3 {watching_server!r}."
            raise ValueError(msg)
        self._server_id64: int = steam_id.id64
        self.server_steam_id = self._server_id64
        self.start()

    @override
    async def fetch_snapshot(self) -> MatchSnapshot:
        stats = await self.bot.steam_web.get_real_time_stats(self._server_id64)
        return MatchSnapshot(
            id=stats.id,
            lobby_type=stats.lobby_type,
            game_mode=stats.game_mode,
            started_at=stats.started_at,
            players=[(player.id, player.hero) for player in stats.players],
            server_steam_id=self._server_id64,
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
        and ``.minimal()`` errors out with ``ValueError`` for them.
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
        """Save the match together with all of its players, not only the friends.

        A coach is not among the players, so such a friend gets no row: the match is not in their match history.
        """
        if not self.has_match_history:
            return
        player_rows = [
            (player.id, self.id, player.hero.id, player.player_slot)
            for player in self.players.values()
            if player.id not in ANONYMOUS_ACCOUNT_IDS
        ]
        async with self.bot.pool.acquire() as connection, connection.transaction():
            # `self.state` instead of `MatchState.Live`: the last friend might have left already, i.e. it's pending
            await connection.execute(
                queries.INSERT_MATCH, self.id, self.started_at, self.lobby_type, self.game_mode, self.state
            )
            await connection.executemany(queries.INSERT_MATCH_PLAYER, player_rows)


def get_or_create[T: LiveMatch](index: dict[str, T], key: str, factory: Callable[[], T]) -> T:
    """Get a match from the index, or create it. Matches the bot gave up on don't get reused.

    Parameters
    ----------
    index
        Matches by their key.
    key
        Key of the wanted match.
    factory
        Creates the match when the index has no usable one. The new match is saved into the index.
    """
    live = index.get(key)
    if live is None or live.state == MatchState.GaveUp:
        live = index[key] = factory()
    return live
