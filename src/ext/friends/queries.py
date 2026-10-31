# Copyright (C) 2020-present, Irene Adler ❤ (@Aluerie).
# Licensed under MPL-2.0, see the LICENSE file for details.
"""SQL queries.

All the queries of ``ext.friends`` in one place, so the tables' usage is easy to review.

Notes
-----
``dota_match_players.friend_id`` is the Steam account id of *any* player of a recorded match, not only of the bot's
friends. The friends are the rows whose ``friend_id`` is also in ``dota_accounts``.
"""

from __future__ import annotations

__all__ = (
    "DELETE_ABANDONED_PENDING_MATCHES",
    "DELETE_MATCHES_BEYOND_KEPT_LIMIT",
    "DELETE_STALE_LIVE_MATCHES",
    "INSERT_ACCOUNT",
    "INSERT_HISTORY_MATCH",
    "INSERT_HISTORY_PLAYER",
    "INSERT_MATCH",
    "INSERT_MATCH_PLAYER",
    "SELECT_PENDING_MATCH_IDS",
    "UPDATE_ACCOUNT_LAST_SEEN",
    "UPDATE_HISTORY_ABANDON",
    "UPDATE_MATCH_FAILED",
    "UPDATE_MATCH_OUTCOME",
    "UPDATE_MATCH_STATE",
)

INSERT_ACCOUNT = "INSERT INTO dota_accounts (friend_id, twitch_id) VALUES ($1, $2) ON CONFLICT (friend_id) DO NOTHING;"
UPDATE_ACCOUNT_LAST_SEEN = "UPDATE dota_accounts SET last_seen = $1 WHERE friend_id = $2;"

INSERT_MATCH = """
    INSERT INTO dota_matches
    (match_id, start_time, lobby_type, game_mode, live)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (match_id) DO NOTHING;
"""
INSERT_MATCH_PLAYER = """
    INSERT INTO dota_match_players
    (friend_id, match_id, hero_id, player_slot)
    VALUES ($1, $2, $3, $4)
    ON CONFLICT (friend_id, match_id) DO NOTHING;
"""
UPDATE_MATCH_STATE = "UPDATE dota_matches SET live = $1 WHERE match_id = $2;"

SELECT_PENDING_MATCH_IDS = "SELECT match_id FROM dota_matches WHERE outcome IS NULL AND live = $1 AND failed < $2;"
UPDATE_MATCH_FAILED = "UPDATE dota_matches SET failed = failed + 1 WHERE match_id = $1;"
UPDATE_MATCH_OUTCOME = "UPDATE dota_matches SET outcome = $1, live = $3 WHERE match_id = $2;"

# A match we already know as completed only needs its `abandon` flag refreshed.
UPDATE_HISTORY_ABANDON = """
    UPDATE dota_match_players mp
    SET abandon = $3
    FROM dota_matches m
    WHERE m.match_id = mp.match_id AND mp.friend_id = $1 AND mp.match_id = $2 AND m.live = $4;
"""
# A match that is still pending (e.g. the bot has given up looking for its outcome) gets its outcome here.
INSERT_HISTORY_MATCH = """
    INSERT INTO dota_matches
        (match_id, start_time, lobby_type, game_mode, outcome, live)
    VALUES ($1, $2, $3, $4, $5, $6)
    ON CONFLICT (match_id) DO
        UPDATE SET live = $6, outcome = $5;
"""
INSERT_HISTORY_PLAYER = """
    INSERT INTO dota_match_players
    (friend_id, match_id, hero_id, player_slot, abandon)
    VALUES ($1, $2, $3, $4, $5)
    ON CONFLICT (friend_id, match_id) DO
        UPDATE SET abandon = $5;
"""

# Only the friends' matches are ranked, otherwise every opponent would keep their only match "recent" forever.
DELETE_MATCHES_BEYOND_KEPT_LIMIT = """
    WITH ranked AS (
        SELECT mp.match_id,
               ROW_NUMBER() OVER (
                   PARTITION BY mp.friend_id
                   ORDER BY m.start_time DESC, m.match_id DESC
               ) AS rn
        FROM dota_match_players mp
        JOIN dota_matches m ON m.match_id = mp.match_id
        WHERE mp.friend_id IN (SELECT friend_id FROM dota_accounts)
    )
    DELETE FROM dota_matches
    WHERE live = $1
      AND match_id NOT IN (SELECT match_id FROM ranked WHERE rn <= $2);
"""
# A match can't stay live for long. Such rows are leftovers of a restart in the middle of a match,
# which is the only way the bot loses track of a live match.
DELETE_STALE_LIVE_MATCHES = """
    DELETE FROM dota_matches
    WHERE live = $1 AND outcome IS NULL AND start_time < $2;
"""
DELETE_ABANDONED_PENDING_MATCHES = """
    DELETE FROM dota_matches
    WHERE live = $1 AND outcome IS NULL AND failed >= $2 AND start_time < $3;
"""
