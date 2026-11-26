# Copyright (C) 2020-present, Irene Adler 💜 (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the friends cog: what happens to matches when friends come and go, and the loops surviving errors."""

from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from steam.ext import dota2

from core import PendingInvite
from ext.friends import queries
from ext.friends.cog import FriendsCog
from ext.friends.matches import PlayingMatch
from ext.friends.models import Friend
from ext.friends.presence import RichPresence
from shared import clock
from shared.apis.local import MatchState

if TYPE_CHECKING:
    import pytest

GAME_ID = "12345"


class FakePool:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        if self.broken:
            raise ConnectionError
        self.executed.append((query, args))
        return "UPDATE 1"

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if self.broken:
            raise ConnectionError
        return []


def make_cog(pool: FakePool) -> FriendsCog:
    """Make the cog without starting its loops."""
    cog = object.__new__(FriendsCog)
    cog.bot = SimpleNamespace(pool=pool, friends_index={}, pending_invites={})  # ty: ignore[invalid-assignment]
    cog.play_matches = {}
    cog.spectate_matches = {}
    cog._reported_statuses = set()
    cog._next_lookup = {}
    cog._unstorable_history = {}
    return cog


def make_friend(friend_id: int = 1, *, playing: bool = True) -> Friend:
    friend = object.__new__(Friend)
    friend.steam = SimpleNamespace(  # ty: ignore[invalid-assignment]
        id=friend_id,
        name="friend",
        app=SimpleNamespace(id=570) if playing else None,
    )
    friend.rich_presence = RichPresence.from_steam({"status": "#DOTA_RP_PLAYING_AS", "WatchableGameID": GAME_ID})
    friend.live_match = None
    return friend


def make_pending_match(cog: FriendsCog) -> PlayingMatch:
    match = object.__new__(PlayingMatch)
    match.friends = set()
    match.id = 7
    match.state = MatchState.Pending
    cog.play_matches[GAME_ID] = match
    return match


class TestReconnect:
    def test_friend_coming_back_makes_a_pending_match_live(self) -> None:
        pool = FakePool()
        cog = make_cog(pool)
        match = make_pending_match(cog)
        friend = make_friend()

        asyncio.run(cog.analyze_rich_presence(friend))

        assert friend.live_match is match
        assert match.state == MatchState.Live
        assert (queries.RESUME_MATCH, (MatchState.Live, 7)) in pool.executed

    def test_leaving_again_makes_it_pending_again(self) -> None:
        pool = FakePool()
        cog = make_cog(pool)
        match = make_pending_match(cog)
        friend = make_friend()

        asyncio.run(cog.analyze_rich_presence(friend))
        asyncio.run(cog.conclude_match(friend))

        assert match.state == MatchState.Pending
        assert pool.executed[-1] == (queries.UPDATE_MATCH_STATE, (MatchState.Pending, 7))

    def test_failing_to_save_the_pending_state_does_not_raise(self, caplog: pytest.LogCaptureFixture) -> None:
        cog = make_cog(FakePool(broken=True))
        match = make_pending_match(cog)
        match.state = MatchState.Live
        friend = make_friend()
        friend.live_match = match
        match.friends.add(friend)

        asyncio.run(cog.conclude_match(friend))

        assert match.state == MatchState.Pending
        assert "failed to save the pending state" in caplog.text


def pending_rows(pool: FakePool, *, failed: int = 0) -> None:
    async def fetch(_query: str, *_args: Any) -> list[dict[str, Any]]:
        return [{"match_id": 7, "failed": failed}]

    pool.fetch = fetch  # ty: ignore[invalid-assignment]


class TestOutcomeLookup:
    def test_completed_match_is_not_resumed(self) -> None:
        pool = FakePool()
        pending_rows(pool)
        cog = make_cog(pool)
        match = make_pending_match(cog)

        async def minimal() -> Any:
            return SimpleNamespace(outcome=dota2.MatchOutcome.RadiantVictory)

        cog.bot.create_partial_match = lambda _: SimpleNamespace(minimal=minimal)  # ty: ignore[invalid-assignment]
        asyncio.run(cog.process_pending_matches.coro(cog))
        assert match.state == MatchState.Completed

        # the friend's Rich Presence shows the finished match once more, then they leave it
        friend = make_friend()
        asyncio.run(cog.analyze_rich_presence(friend))
        asyncio.run(cog.conclude_match(friend))

        assert match.state == MatchState.Completed
        assert not any(query in {queries.RESUME_MATCH, queries.UPDATE_MATCH_STATE} for query, _ in pool.executed)

    def test_timeout_counts_as_a_failed_lookup_and_backs_off(self) -> None:
        pool = FakePool()
        pending_rows(pool, failed=3)
        cog = make_cog(pool)
        lookups = 0

        async def minimal() -> Any:
            nonlocal lookups
            lookups += 1
            raise TimeoutError

        cog.bot.create_partial_match = lambda _: SimpleNamespace(minimal=minimal)  # ty: ignore[invalid-assignment]
        asyncio.run(cog.process_pending_matches.coro(cog))
        asyncio.run(cog.process_pending_matches.coro(cog))

        assert lookups == 1
        assert pool.executed == [(queries.UPDATE_MATCH_FAILED, (7,))]

    def test_matches_no_longer_pending_are_forgotten(self) -> None:
        cog = make_cog(FakePool())
        cog._next_lookup[7] = 0.0
        asyncio.run(cog.process_pending_matches.coro(cog))
        assert cog._next_lookup == {}


class TestFriendRemove:
    def test_removed_friend_is_forgotten_and_leaves_their_match(self) -> None:
        cog = make_cog(FakePool())
        match = make_pending_match(cog)
        match.state = MatchState.Live
        friend = make_friend()
        friend.live_match = match
        match.friends.add(friend)
        cog.bot.friends_index[1] = friend

        asyncio.run(cog.on_friend_remove(friend.steam))

        assert cog.bot.friends_index == {}
        assert friend.live_match is None
        assert match.state == MatchState.Pending

    def test_unknown_friend(self) -> None:
        cog = make_cog(FakePool())
        asyncio.run(cog.on_friend_remove(make_friend().steam))


class TestLoopsSurviveDatabaseErrors:
    def test_pending_matches(self, caplog: pytest.LogCaptureFixture) -> None:
        cog = make_cog(FakePool(broken=True))
        asyncio.run(cog.process_pending_matches.coro(cog))
        assert "Failed to get the pending matches" in caplog.text

    def test_outcome_lookup(self, caplog: pytest.LogCaptureFixture) -> None:
        pool = FakePool(broken=True)

        async def fetch(_query: str, *_args: Any) -> list[dict[str, Any]]:
            return [{"match_id": 7, "failed": 0}]

        pool.fetch = fetch  # ty: ignore[invalid-assignment]
        cog = make_cog(pool)

        async def minimal() -> Any:
            raise dota2.MatchNotFound(None, match_id=7)

        cog.bot.create_partial_match = lambda _: SimpleNamespace(minimal=minimal)  # ty: ignore[invalid-assignment]
        asyncio.run(cog.process_pending_matches.coro(cog))
        assert "Failed to get the outcome of the match 7" in caplog.text

    def test_clean_up_still_forgets_old_invites(self, caplog: pytest.LogCaptureFixture) -> None:
        cog = make_cog(FakePool(broken=True))
        cog.bot.pending_invites[1] = PendingInvite(clock.utcnow() - dt.timedelta(hours=1), "42")
        asyncio.run(cog.remove_way_too_old_matches.coro(cog))
        assert "Failed to remove old matches from the database" in caplog.text
        assert cog.bot.pending_invites == {}
