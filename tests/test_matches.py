# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for the match index helper."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ext.friends.matches import get_or_create
from shared.apis.local import MatchState


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
