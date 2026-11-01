# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Tests for Rich Presence parsing and classification."""

from __future__ import annotations

from ext.friends.presence import (
    Dashboard,
    Incomplete,
    PlayingPartialMatch,
    RichPresence,
    SpectatingPartialMatch,
    Status,
    Unrecognized,
    UnsupportedPartialMatch,
    classify,
)


def classify_raw(raw: dict[str, str] | None) -> object:
    """Parse and classify a raw Rich Presence."""
    return classify(RichPresence.from_steam(raw))


class TestRichPresence:
    def test_offline(self) -> None:
        assert RichPresence.from_steam(None).status == Status.RichPresenceNone

    def test_noisy_keys_only_is_offline(self) -> None:
        assert RichPresence.from_steam({"param1": "12"}).status == Status.RichPresenceNone

    def test_missing_status(self) -> None:
        assert RichPresence.from_steam({"something": "else"}).status == Status.NoStatus

    def test_known_status(self) -> None:
        assert RichPresence.from_steam({"status": "#DOTA_RP_INIT"}).status == Status.MainMenu

    def test_prefix_status(self) -> None:
        assert Status.try_value("#DOTA_Crownfall_Whatever") == Status.Crownfall

    def test_display_name(self) -> None:
        assert Status.Playing.display_name == "Playing"

    def test_ignored_keys_do_not_affect_equality(self) -> None:
        one = RichPresence.from_steam({"status": "#DOTA_RP_PLAYING_AS", "param1": "3"})
        two = RichPresence.from_steam({"status": "#DOTA_RP_PLAYING_AS", "param1": "4"})
        assert one == two

    def test_meaningful_keys_affect_equality(self) -> None:
        one = RichPresence.from_steam({"status": "#DOTA_RP_PLAYING_AS", "WatchableGameID": "1"})
        two = RichPresence.from_steam({"status": "#DOTA_RP_PLAYING_AS", "WatchableGameID": "2"})
        assert one != two

    def test_party_ids(self) -> None:
        # account id 123456 and 654321 as Steam ID64
        id64_one = 76561197960265728 + 123456
        id64_two = 76561197960265728 + 654321
        raw = {
            "party": f"members {{ steam_id: {id64_one} }} ",
            "party2": f"members {{ steam_id: {id64_two} }}",
        }
        assert RichPresence.from_steam(raw).party_ids == [123456, 654321]

    def test_no_party(self) -> None:
        assert RichPresence.from_steam({"status": "#DOTA_RP_INIT"}).party_ids == []


class TestClassify:
    def test_dashboard(self) -> None:
        assert classify_raw({"status": "#DOTA_RP_INIT"}) == Dashboard()

    def test_playing(self) -> None:
        raw = {"status": "#DOTA_RP_PLAYING_AS", "WatchableGameID": "123"}
        assert classify_raw(raw) == PlayingPartialMatch("123")

    def test_playing_with_zero_game_id_is_incomplete(self) -> None:
        raw = {"status": "#DOTA_RP_PLAYING_AS", "WatchableGameID": "0"}
        assert isinstance(classify_raw(raw), Incomplete)

    def test_playing_without_game_id_is_incomplete(self) -> None:
        assert isinstance(classify_raw({"status": "#DOTA_RP_PLAYING_AS"}), Incomplete)

    def test_demo_mode(self) -> None:
        raw = {"status": "#DOTA_RP_PLAYING_AS", "param0": "#demo_hero_mode_name"}
        assert classify_raw(raw) == UnsupportedPartialMatch("Demo mode is not supported")

    def test_bot_match_lobby(self) -> None:
        raw = {"status": "#DOTA_RP_PLAYING_AS", "param0": "#DOTA_lobby_type_name_bot_match"}
        assert classify_raw(raw) == UnsupportedPartialMatch("Bot matches are not supported")

    def test_spectating(self) -> None:
        raw = {"status": "#DOTA_RP_SPECTATING", "watching_server": "[A:1:2:3]"}
        assert classify_raw(raw) == SpectatingPartialMatch("[A:1:2:3]")

    def test_watching_replay(self) -> None:
        assert classify_raw({"status": "#DOTA_RP_SPECTATING"}) == UnsupportedPartialMatch(
            "Watching replays is not supported"
        )

    def test_unsupported_status(self) -> None:
        assert classify_raw({"status": "#DOTA_RP_BOTPRACTICE"}) == UnsupportedPartialMatch(
            "Bot matches are not supported"
        )

    def test_closed_dota(self) -> None:
        assert classify_raw({"something": "else"}) == Incomplete("Closed Dota")

    def test_offline(self) -> None:
        assert classify_raw(None) == Incomplete("No Rich Presence yet")

    def test_unrecognized_status(self) -> None:
        assert classify_raw({"status": "#DOTA_RP_SOMETHING_NEW"}) == Unrecognized("#DOTA_RP_SOMETHING_NEW")
