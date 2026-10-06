from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Any, override

from steam.ext import commands, dota2

from core import Dota2Bot
from shared import clock
from shared.concepts import tasks

from . import activities, enums
from .models import PlayingMatch, RichPresence, SpectatingMatch, Streamer, UnsupportedMatch

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)


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
            self.bot.streamers[friend.id] = Streamer(self.bot, friend._user)  # ruff: ignore[private-member-access]

        for streamer in self.bot.streamers.values():
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

        streamer = self.bot.streamers.setdefault(after.id, Streamer(self.bot, after))
        streamer.rich_presence = rp_after
        log.debug("Recognized rich presence update for %r: %r", streamer, streamer.rich_presence)
        await self.analyze_rich_presence(streamer)

    async def analyze_rich_presence(self, streamer: Streamer) -> None:
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
            streamer.activity = activities.Incomplete("Not Green In Dota")
            await self.conclude_match(streamer)
            return

        if not await streamer.update_activity():
            # No new activity was detected
            return

        match streamer.activity:
            case activities.Dashboard():
                await self.conclude_match(streamer)

            case activities.PlayingPartialMatch():
                watchable_game_id = streamer.activity.watchable_game_id
                if watchable_game_id not in self.bot.play_matches:
                    self.bot.play_matches[watchable_game_id] = PlayingMatch(self.bot, watchable_game_id)
                streamer.live_match = self.bot.play_matches[watchable_game_id]

            case activities.SpectatingPartialMatch():
                watching_server = streamer.activity.watching_server
                if watching_server not in self.bot.spectate_matches:
                    self.bot.spectate_matches[watching_server] = SpectatingMatch(self.bot, watching_server)
                streamer.live_match = self.bot.spectate_matches[watching_server]

            case activities.UnsupportedPartialMatch():
                streamer.live_match = UnsupportedMatch(self.bot, message=streamer.activity.msg)

            case _:
                # Incomplete activities - wait for confirmed activities.
                # Do nothing.
                return

    #########################################################################################################################
    # PENDING MATCHES
    #########################################################################################################################

    async def conclude_match(self, streamer: Streamer) -> None:
        """Conclude match as finished.

        This nulls `Friend.live_match` attribute as well as adds the match into the database
        if it's a `PlayingMatch`.
        """
        match = streamer.live_match
        if isinstance(match, PlayingMatch) and match.match_id:
            if match.state == enums.PlayingMatchState.Live:
                match.state = enums.PlayingMatchState.Pending
                query = "UPDATE ttv_dota_matches SET live = $1 WHERE match_id = $2;"
                await self.bot.pool.execute(query, enums.PlayingMatchState.Pending, match.match_id)
                if not self.process_pending_matches.is_running():
                    self.process_pending_matches.start()
            elif match.state == enums.PlayingMatchState.Pending.Starting:
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

        query = "SELECT match_id, failed FROM ttv_dota_matches WHERE outcome IS NULL AND live = $1 AND failed < 12;"
        rows = await self.bot.pool.fetch(query, enums.PlayingMatchState.Pending)
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
            await self.bot.pool.execute(query, minimal.outcome, row["match_id"], enums.PlayingMatchState.Completed)

    #########################################################################################################################
    # MATCH HISTORY AND MATCHES DATABASE CARE
    #########################################################################################################################

    async def update_mmr(
        self,
        *,
        friend_id: int,
        lobby_type: int,
        player_slot: int,
        outcome: dota2.MatchOutcome,
        is_abandon: bool,
    ) -> None:
        """Update MMR for an account under friend_id.

        We only update mmr using match history because it has abandon information.
        Alternative is using Opendota.
        Stratz is too slow - they only fill api when the match is fully parsed which can take 10+ minutes.
        """
        if lobby_type != dota2.LobbyType.Ranked:
            return
        if outcome >= dota2.MatchOutcome.NotScoredPoorNetworkConditions:
            return

        if is_abandon:
            mmr_delta = -25
        elif outcome == dota2.MatchOutcome.RadiantVictory:
            mmr_delta = 25 if player_slot < 5 else -25
        elif outcome == dota2.MatchOutcome.DireVictory:
            mmr_delta = 25 if player_slot > 4 else -25
        else:
            mmr_delta = 0

        if mmr_delta:
            query = "UPDATE ttv_dota_accounts SET estimated_mmr = estimated_mmr + $1 WHERE friend_id = $2;"
            await self.bot.pool.execute(query, mmr_delta, friend_id)

    @tasks.loop(hours=1)
    async def double_check_gc_match_history(self) -> None:
        """Double check match history in case missed some games.

        Useful to keep W-L as precise as possible.
        """
        for streamer in self.bot.streamers.values():
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
            ON CONFLICT (match_id) DO NOTHING;
        """
        await self.bot.pool.execute(
            query,
            match.id,
            match.start_time,
            match.lobby_type,
            match.game_mode,
            minimal.outcome,
            enums.PlayingMatchState.Completed,
        )
        player_slot = next((slot for slot, player in enumerate(minimal.players) if player.hero == match.hero), None)
        assert player_slot is not None, "Somehow `player_slot` is `None` in match history match"

        # MMR update only happens there because match history has abandon information while minimal doesn't.
        await self.update_mmr(
            friend_id=friend_id,
            lobby_type=match.lobby_type,
            player_slot=player_slot,
            outcome=minimal.outcome,
            is_abandon=match.abandon,
        )

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

    # class PendingAbandonsQueryRow(TypedDict):
    #     friend_id: int
    #     match_id: int
    #     outcome: int
    #     lobby_type: int
    #     player_slot: int

    # @ireloop(seconds=20)
    # async def process_pending_abandons(self) -> None:
    #     """Process pending abandons.

    #     This task is separate from pending matches due to fetching matches from opendota.
    #     """
    #     query = """
    #         SELECT p.match_id, p.friend_id, p.player_slot, m.outcome, m.lobby_type
    #         FROM ttv_dota_match_players p
    #         JOIN ttv_dota_matches m ON m.match_id = p.match_id
    #         WHERE abandon IS NULL;
    #     """
    #     rows: list[PendingAbandonsQueryRow] = await self.bot.pool.fetch(query)
    #     if not rows:
    #         # I guess no pending matches left
    #         self.process_pending_abandons.cancel()
    #         return

    #     players_cache: dict[int, list[schemas.MatchesPlayer]] = {}

    #     for row in rows:
    #         players = players_cache.get(row["match_id"])
    #         if not players:
    #             match = await self.bot.opendota.matches(row["match_id"])
    #             try:
    #                 match["players"]
    #             except KeyError:
    #                 continue
    #             else:
    #                 players = match["players"]
    #                 players_cache[match["match_id"]] = match["players"]

    #         player = players[row["player_slot"]]
    #         is_abandon = bool(player["abandons"])
    #         query = """
    #             UPDATE ttv_dota_match_players
    #             SET abandon = $1
    #             WHERE match_id = $2 AND friend_id = $3;
    #         """
    #         await self.bot.pool.execute(query, is_abandon, row["match_id"], row["friend_id"])
    #         await self.update_mmr(
    #             friend_id=row["friend_id"],
    #             lobby_type=row["lobby_type"],
    #             player_slot=row["player_slot"],
    #             outcome=row["outcome"],
    #             is_abandon=is_abandon,
    #         )

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
