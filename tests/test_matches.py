# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the live matches: the match index helper and the collected match data."""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, override

import pytest
from steam.ext import dota2

from ext.friends import queries
from ext.friends.matches import MatchSnapshot, PlayingMatch, TrackedMatch, get_or_create
from ext.friends.models import Player
from shared import clock
from shared.apis.local import MatchState

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable


def make_match(state: MatchState = MatchState.Starting) -> Any:
    """Make a stand-in for a live match, only its state matters here."""
    return SimpleNamespace(state=state)


def test_creates_when_missing() -> None:
    index: dict[str, Any] = {}
    live = get_or_create(index, "key", make_match)
    assert index["key"] is live


def test_reuses_existing() -> None:
    index: dict[str, Any] = {}
    first = get_or_create(index, "key", make_match)
    second = get_or_create(index, "key", make_match)
    assert first is second


def test_replaces_match_that_the_bot_gave_up_on() -> None:
    index: dict[str, Any] = {}
    first = get_or_create(index, "key", lambda: make_match(MatchState.GaveUp))
    second = get_or_create(index, "key", make_match)
    assert first is not second
    assert index["key"] is second


def test_keeps_live_and_pending_matches() -> None:
    for state in (MatchState.Live, MatchState.Pending):
        index = {"key": make_match(state)}
        assert get_or_create(index, "key", make_match) is index["key"]


# --- live match data -------------------------------------------------------------------------------------------------


class FakePool:
    """Records the queries, and lets a test run code in the middle of a transaction."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.during_transaction: Callable[[], None] = lambda: None

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "UPDATE 1"

    async def executemany(self, query: str, rows: list[Any]) -> None:
        self.executed.append((query, (rows,)))
        # the last statement of `on_live`'s transaction, i.e. the commit is next
        self.during_transaction()

    @contextlib.asynccontextmanager
    async def acquire(self) -> AsyncGenerator[Any]:
        yield SimpleNamespace(execute=self.execute, executemany=self.executemany, transaction=contextlib.nullcontext)


NO_BOT: Any = None


class FakeMatch(TrackedMatch):
    """Tracked match that doesn't poll on its own."""

    @override
    async def fetch_snapshot(self) -> MatchSnapshot:
        raise NotImplementedError


def make_snapshot(players: list[tuple[int, Any]]) -> MatchSnapshot:
    return MatchSnapshot(
        id=7,
        lobby_type=dota2.LobbyType.Ranked,
        game_mode=dota2.GameMode.AllPick,
        started_at=clock.utcnow(),
        players=players,
    )


@pytest.fixture
def profile_cards(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace the profile card requests, the account ids that would be requested get collected."""
    requested: list[int] = []

    async def create(_: Any, account_id: int, player_slot: int, hero: Any) -> Player:
        requested.append(account_id)
        return Player(account_id, player_slot, 100, "Herald", hero)

    monkeypatch.setattr(Player, "create", create)
    return requested


class TestApplySnapshot:
    def test_hidden_profiles_are_separate_players(self, profile_cards: list[int]) -> None:
        match = FakeMatch(NO_BOT, "playing")
        asyncio.run(match._apply(make_snapshot([(10, "a"), (0, "b"), (0, "c"), (4294967295, "d")])))

        assert [(p.player_slot, p.id, p.hero) for p in match.players.values()] == [
            (0, 10, "a"),
            (1, 0, "b"),
            (2, 0, "c"),
            (3, 4294967295, "d"),
        ]
        # hidden profiles have no profile card to fetch
        assert profile_cards == [10]

    def test_known_players_only_get_their_hero_refreshed(self, profile_cards: list[int]) -> None:
        match = FakeMatch(NO_BOT, "playing")
        asyncio.run(match._apply(make_snapshot([(10, ""), (0, "")])))
        first = match.players[0]
        asyncio.run(match._apply(make_snapshot([(10, "a"), (0, "b")])))

        assert match.players[0] is first
        assert [p.hero for p in match.players.values()] == ["a", "b"]
        assert profile_cards == [10]


class UnpolledPlayingMatch(PlayingMatch):
    """Playing match that doesn't poll on its own."""

    @override
    def start(self) -> None:
        pass


class TestOnLive:
    def make_match(self, pool: FakePool) -> PlayingMatch:
        bot: Any = SimpleNamespace(pool=pool)
        match = UnpolledPlayingMatch(bot, "123")
        match.id = 7
        match.lobby_type = dota2.LobbyType.Ranked
        match.state = MatchState.Live
        return match

    def test_saves_the_match(self) -> None:
        pool = FakePool()
        asyncio.run(self.make_match(pool).on_live())
        assert [query for query, _ in pool.executed] == [queries.INSERT_MATCH, queries.INSERT_MATCH_PLAYER]

    def test_friend_leaving_before_the_commit_is_not_lost(self) -> None:
        pool = FakePool()
        match = self.make_match(pool)

        def last_friend_leaves() -> None:
            match.state = MatchState.Pending

        pool.during_transaction = last_friend_leaves
        asyncio.run(match.on_live())

        assert pool.executed[-1] == (queries.UPDATE_MATCH_STATE, (MatchState.Pending, 7))
