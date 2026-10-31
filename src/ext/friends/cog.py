# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""Friends cog.

Follows the bot's Steam friends through Dota 2 Rich Presence and inspects their live matches.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import logging
import pprint
from typing import TYPE_CHECKING, override

import steam
from steam.ext import commands

from core import Dota2Bot
from shared import clock
from shared.apis.local import MatchState
from shared.concepts import tasks

from . import queries
from .matches import PlayingMatch, SpectatingMatch, TrackedMatch, UnsupportedMatch, get_or_create
from .models import Friend
from .presence import (
    Dashboard,
    PlayingPartialMatch,
    RichPresence,
    SpectatingPartialMatch,
    Unrecognized,
    UnsupportedPartialMatch,
    classify,
)

if TYPE_CHECKING:
    from steam.ext import dota2

    from .matches import LiveMatch

__all__ = ("FriendsCog",)

log = logging.getLogger(__name__)

GC_READY_TIMEOUT = 11 * 60  # seconds the Game Coordinator gets to get ready before the bot is restarted
FRIEND_LIST_RETRY_DELAY = 30  # seconds before another attempt to get the bot's friend list
MAX_FAILED_LOOKUPS = 17  # pending matches that failed this many outcome lookups are ignored
MATCH_HISTORY_MAX_AGE = dt.timedelta(hours=48)
LIVE_MATCH_MAX_AGE = dt.timedelta(hours=8)
MATCHES_KEPT_PER_FRIEND = 20
PENDING_INVITE_TTL = dt.timedelta(minutes=10)


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
        self.process_pending_matches.start()
        self.double_check_gc_match_history.start()
        self.remove_way_too_old_matches.start()

    @override
    async def cog_unload(self) -> None:
        """Stop all the loops and the polling of matches that are still collecting their data."""
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
        """Add a friend of the bot into the index, unless they are there already.

        Parameters
        ----------
        steam_friend
            Friend from the bot's Steam friend list.

        Returns
        -------
        Friend
            The indexed friend.
        """
        friend = self.bot.friends_index.get(steam_friend.id)
        if friend is None:
            # we need `friend._user` because `Friend` class doesn't have Dota 2 features which User class have.
            friend = self.bot.friends_index[steam_friend.id] = Friend(steam_friend._user)  # ruff: ignore[private-member-access]  # ty: ignore[invalid-argument-type]
        return friend

    @tasks.loop(count=1)
    async def fill_friends(self) -> None:
        """Index bot's friend list and analyze their rich presences."""
        log.debug("Indexing bot's friend list.")
        while True:
            try:
                steam_friends = await self.bot.user.friends()
            except Exception:
                log.exception("Failed to get the bot's friend list, retrying in %d seconds.", FRIEND_LIST_RETRY_DELAY)
                await asyncio.sleep(FRIEND_LIST_RETRY_DELAY)
            else:
                break

        for steam_friend in steam_friends:
            self._index_friend(steam_friend)

        # From now on `on_user_update` handles changes, so none get lost while the initial analysis is running.
        self.bot.friend_index_ready.set()
        log.debug("Friends index ready.")

        for friend in list(self.bot.friends_index.values()):
            try:
                await self.analyze_rich_presence(friend)
            except Exception:
                log.exception("Failed to analyze rich presence of %r", friend)

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_user_update(self, _: dota2.User, after: dota2.User) -> None:
        """Handle a Steam user getting updated, due to one or more of their attributes changing.

        Parameters
        ----------
        _
            The user before the update.
        after
            The user after the update.
        """
        # Only the bot's friends are tracked: they get indexed on startup and in ``on_friend_add``.
        # Other users, such as party members and lobby players, also trigger this event.
        friend = self.bot.friends_index.get(after.id)
        if friend is None:
            return

        friend.steam = after
        rich_presence = RichPresence.from_steam(after.rich_presence)
        if friend.rich_presence == rich_presence:
            return

        friend.rich_presence = rich_presence
        log.debug("Recognized rich presence update for %r: %r", friend, rich_presence)
        if self.bot.friend_index_ready.is_set():
            await self.analyze_rich_presence(friend)
        # otherwise ``fill_friends`` is going to analyze everyone once it's done

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

        await self.bot.pool.execute(queries.UPDATE_ACCOUNT_LAST_SEEN, clock.utcnow(), friend.steam.id)

        match classify(friend.rich_presence):
            case Dashboard():
                await self.conclude_match(friend)
            case PlayingPartialMatch(watchable_game_id=game_id):
                live = get_or_create(self.play_matches, game_id, lambda: PlayingMatch(self.bot, game_id))
                await self._attach(friend, live)
            case SpectatingPartialMatch(watching_server=server):
                live = get_or_create(self.spectate_matches, server, lambda: SpectatingMatch(self.bot, server))
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
        live.add_friend(friend)

    async def _report_unrecognized(self, friend: Friend, status: str) -> None:
        if status in self._reported_statuses:
            return
        self._reported_statuses.add(status)
        rich_presence = friend.rich_presence
        text = (
            "Uncategorized Rich Presence Status\n"
            f"friend=`{friend!r}`\n"
            f"status=`{rich_presence.status.value}`\n"
            # Discord messages are limited to 2000 characters
            f"rich_presence.raw=```json\n{pprint.pformat(rich_presence.raw)[:1200]}```"
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
            log.info("%r: abandoned, the last friend %r left before the match data was complete", live, friend)
            live.cancel()
        elif isinstance(live, PlayingMatch) and live.state == MatchState.Live:
            log.info("%r: pending, the last friend %r left", live, friend)
            live.state = MatchState.Pending
            await self.bot.pool.execute(queries.UPDATE_MATCH_STATE, MatchState.Pending, live.id)

    @tasks.loop(seconds=20)
    async def process_pending_matches(self) -> None:
        """Find out the outcome of pending matches.

        Runs all the time: with no pending matches it's one cheap query every 20 seconds.
        That also picks up matches left pending by the previous run.

        Notes
        -----
        * We use match history endpoint specifically because it has ``.abandon`` attribute.
          So if we remove that quirk - we can probably be fine with just "minimal_match" data.
        * Another option is to use opendota api (stratz is too slow -
          they don't allow access to data until full parse is done)
        """
        rows = await self.bot.pool.fetch(queries.SELECT_PENDING_MATCH_IDS, MatchState.Pending, MAX_FAILED_LOOKUPS)

        for row in rows:
            match_id = row["match_id"]
            try:
                # If friend disconnects before ancient falls (e.g., preemptive disconnects or
                # when game is "Safe to leave") then ``.minimal`` won't give any results as the game
                # is still live but friend's RP is different. This also happens if friend
                # disconnects-reconnects in the middle of the match.
                minimal = await self.bot.create_partial_match(match_id).minimal()
            except ValueError:
                # matches that failed too many times get ignored by the query above
                await self.bot.pool.execute(queries.UPDATE_MATCH_FAILED, match_id)
                continue
            except Exception:
                # e.g. Game Coordinator timeouts. This loop never stops, so it must survive them.
                log.exception("Failed to get the outcome of the match %s", match_id)
                continue

            await self.bot.pool.execute(queries.UPDATE_MATCH_OUTCOME, minimal.outcome, match_id, MatchState.Completed)
            log.info("Match %s is completed with the outcome %r", match_id, minimal.outcome)

    # --- match history and database care ---

    @tasks.loop(hours=1)
    async def double_check_gc_match_history(self) -> None:
        """Double check match history in case missed some games.

        Useful to keep W-L as precise as possible. Also the only source of the ``abandon`` flag.
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
        """Save a match history entry of a friend, unless it is too old."""
        if entry.start_time < clock.utcnow() - MATCH_HISTORY_MAX_AGE:
            return

        # Fast path: a match we already know as completed only needs its ``abandon`` flag refreshed,
        # which comes from the match history entry. No need to ask for ``.minimal()`` again.
        status = await self.bot.pool.execute(
            queries.UPDATE_HISTORY_ABANDON, friend_id, entry.id, entry.abandon, MatchState.Completed
        )
        if status == "UPDATE 1":
            return

        # match history entities on their own do not give proper outcome (Radiant/Dire)
        minimal = await self.bot.create_partial_match(entry.id).minimal()
        player_slot = next((slot for slot, player in enumerate(minimal.players) if player.hero == entry.hero), None)
        if player_slot is None:
            msg = f"Hero of match history match {entry.id} is not among the match players."
            raise LookupError(msg)

        await self.bot.pool.execute(
            queries.INSERT_HISTORY_MATCH,
            entry.id,
            entry.start_time,
            entry.lobby_type,
            entry.game_mode,
            minimal.outcome,
            MatchState.Completed,
        )
        await self.bot.pool.execute(
            queries.INSERT_HISTORY_PLAYER, friend_id, entry.id, entry.hero.id, player_slot, entry.abandon
        )

    @tasks.loop(hours=6)
    async def remove_way_too_old_matches(self) -> None:
        """Clean up old matches from the database and from the bot's memory.

        Only the latest matches of each friend are kept in the database (together with all their players).
        Matches that are still live or pending are not removed, unless the bot has given up on their outcome long ago.
        Matches that started more than 8 hours ago are forgotten by the in-memory indexes.
        """
        log.debug("Removing way too old matches.")
        await self.bot.pool.execute(
            queries.DELETE_MATCHES_BEYOND_KEPT_LIMIT, MatchState.Completed, MATCHES_KEPT_PER_FRIEND
        )
        await self.bot.pool.execute(
            queries.DELETE_STALE_LIVE_MATCHES, MatchState.Live, clock.utcnow() - LIVE_MATCH_MAX_AGE
        )
        await self.bot.pool.execute(
            queries.DELETE_ABANDONED_PENDING_MATCHES,
            MatchState.Pending,
            MAX_FAILED_LOOKUPS,
            clock.utcnow() - MATCH_HISTORY_MAX_AGE,
        )

        cut_off = clock.utcnow() - LIVE_MATCH_MAX_AGE
        for index in (self.play_matches, self.spectate_matches):
            for key in [key for key, live in index.items() if live.started_at < cut_off]:
                del index[key]

        # invites that were never used would stay in memory forever otherwise
        invite_cut_off = clock.utcnow() - PENDING_INVITE_TTL
        for friend_id in [fid for fid, invite in self.bot.pending_invites.items() if invite.sent_at < invite_cut_off]:
            del self.bot.pending_invites[friend_id]

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
                process = await asyncio.create_subprocess_exec("sudo", "systemctl", "restart", "dota2bot")
                await process.wait()
            except OSError:
                log.exception("Failed to restart the bot's process")
            await self.bot.wait_until_gc_ready()

    @double_check_gc_match_history.before_loop
    @remove_way_too_old_matches.before_loop
    @process_pending_matches.before_loop
    async def wait_for_friend_index(self) -> None:
        """Wait for ``fill_friends``, which itself waits for all the clients, to fill the friend index."""
        await self.bot.friend_index_ready.wait()

    # --- invites and friends ---

    @commands.Cog.listener()  # ty: ignore[invalid-argument-type]
    async def on_invite(self, invite: steam.Invite) -> None:
        """Accept user invites that the bot has asked for recently, decline non-user invites, ignore the rest.

        Parameters
        ----------
        invite
            The invite the bot has received.
        """
        log.info("Received invite: %r", invite)
        if not isinstance(invite, steam.UserInvite):
            method = getattr(invite, "decline", None)
            if inspect.iscoroutinefunction(method):
                await method()
            return

        pending = self.bot.pending_invites.get(invite.author.id)
        if pending is not None and clock.utcnow() - pending.sent_at < PENDING_INVITE_TTL:
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
        """Save the new friend's account, index them and sync their match history.

        Parameters
        ----------
        steam_friend
            The user who has just become the bot's friend.
        """
        log.info("Added friend %r", steam_friend)
        pending = self.bot.pending_invites.pop(steam_friend.id, None)
        if pending is None:
            # ``twitch_id`` is NOT NULL, so there is no account to save for someone the website doesn't know
            log.warning("Friend %r was added without a pending invite, not saving the account.", steam_friend)
        else:
            await self.bot.pool.execute(queries.INSERT_ACCOUNT, steam_friend.id, pending.twitch_id)

        friend = self._index_friend(steam_friend)
        await self.analyze_rich_presence(friend)
        await self.bot.wait_until_gc_ready()
        await self._sync_history(friend)
