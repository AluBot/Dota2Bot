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

from ext.friends import matches, queries
from ext.friends.matches import (
    LiveMatchBatcher,
    MatchSnapshot,
    PlayingMatch,
    TrackedMatch,
    get_or_create,
    seconds_until_next_poll,
)
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

    def test_silent_profile_card_shows_unknown_and_is_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        requested: list[int] = []

        async def create(_: Any, account_id: int, player_slot: int, hero: Any) -> Player:
            requested.append(account_id)
            if account_id == 11 and requested.count(11) == 1:
                raise TimeoutError
            return Player(account_id, player_slot, 100, "Herald", hero)

        monkeypatch.setattr(Player, "create", create)
        match = FakeMatch(NO_BOT, "playing")
        asyncio.run(match._apply(make_snapshot([(10, ""), (11, "")])))

        assert [(p.id, p.rank_display_name, p.profile_unknown) for p in match.players.values()] == [
            (10, "Herald", False),
            (11, "Unknown", True),
        ]

        asyncio.run(match._apply(make_snapshot([(10, "a"), (11, "b")])))

        assert [(p.id, p.rank_display_name, p.hero) for p in match.players.values()] == [
            (10, "Herald", "a"),
            (11, "Herald", "b"),
        ]
        assert requested == [10, 11, 11]


class UnpolledPlayingMatch(PlayingMatch):
    """Playing match that doesn't poll on its own."""

    @override
    def start(self) -> None:
        pass


class TestOnLive:
    def make_match(self, pool: FakePool) -> PlayingMatch:
        bot: Any = SimpleNamespace(pool=pool)
        match = UnpolledPlayingMatch(bot, "123", LiveMatchBatcher(fetch_nothing))
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


async def fetch_nothing(_lobby_ids: list[int]) -> list[Any]:
    return []


@pytest.fixture
def no_batch_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(matches, "BATCH_WINDOW", 0)


@pytest.mark.usefixtures("no_batch_window")
class TestLiveMatchBatcher:
    def test_lookups_made_together_share_one_request(self) -> None:
        requests: list[list[int]] = []

        async def fetch(lobby_ids: list[int]) -> list[Any]:
            requests.append(lobby_ids)
            return [SimpleNamespace(lobby_id=lobby_id) for lobby_id in lobby_ids if lobby_id != 3]

        async def main() -> tuple[Any, ...]:
            batcher = LiveMatchBatcher(fetch)
            return await asyncio.gather(batcher.get(1), batcher.get(2), batcher.get(3), batcher.get(1))

        results = asyncio.run(main())
        assert requests == [[1, 2, 3]]
        assert [getattr(result, "lobby_id", None) for result in results] == [1, 2, None, 1]

    def test_later_lookups_get_their_own_request(self) -> None:
        requests: list[list[int]] = []

        async def fetch(lobby_ids: list[int]) -> list[Any]:
            requests.append(lobby_ids)
            return []

        async def main() -> None:
            batcher = LiveMatchBatcher(fetch)
            await batcher.get(1)
            await batcher.get(2)

        asyncio.run(main())
        assert requests == [[1], [2]]

    def test_a_failed_request_fails_every_lookup_in_it(self) -> None:
        async def fetch(_lobby_ids: list[int]) -> list[Any]:
            raise TimeoutError

        async def main() -> tuple[Any, ...]:
            batcher = LiveMatchBatcher(fetch)
            return await asyncio.gather(batcher.get(1), batcher.get(2), return_exceptions=True)

        assert [type(result) for result in asyncio.run(main())] == [TimeoutError, TimeoutError]

    def test_a_cancelled_lookup_does_not_break_the_others(self) -> None:
        async def fetch(lobby_ids: list[int]) -> list[Any]:
            return [SimpleNamespace(lobby_id=lobby_id) for lobby_id in lobby_ids]

        async def main() -> Any:
            batcher = LiveMatchBatcher(fetch)
            cancelled = asyncio.create_task(batcher.get(1))
            kept = asyncio.create_task(batcher.get(2))
            await asyncio.sleep(0)
            cancelled.cancel()
            return await kept

        assert asyncio.run(main()).lobby_id == 2


def test_polls_wait_for_the_shared_clock() -> None:
    for _ in range(100):
        delay = seconds_until_next_poll()
        assert matches.POLL_INTERVAL / 2 <= delay <= matches.POLL_INTERVAL * 1.5
