/* ---------------------------------------------------------------------------------------------------------------------
Baseline: the `dota2bot` schema as it was on prod on 2026-11-26.

IreBot reads these tables too (`mmrbot`). `dota_accounts.twitch_id` references IreBot's `irenesbot.tokens`,
so on an empty database IreBot's baseline runs first.
--------------------------------------------------------------------------------------------------------------------- */

CREATE SCHEMA dota2bot;

/* --- Dota constants ----------------------------------------------------------------------------------------------- */

CREATE TABLE dota2bot.dota_constants_items (
    item_id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL
);

/* --- Dota friends ------------------------------------------------------------------------------------------------- */

CREATE TABLE dota2bot.dota_accounts (
    friend_id BIGINT PRIMARY KEY, -- steam32 ID (friend ID)
    twitch_id TEXT NOT NULL,
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_twitch_id FOREIGN KEY (twitch_id) REFERENCES irenesbot.tokens (user_id) ON DELETE CASCADE
);

CREATE TABLE dota2bot.dota_matches (
    match_id BIGINT PRIMARY KEY,
    start_time TIMESTAMPTZ DEFAULT NOW(),
    lobby_type INTEGER NOT NULL,
    game_mode INTEGER NOT NULL,
    outcome INTEGER,
    live INTEGER DEFAULT 1,
    failed INTEGER DEFAULT 0
);

CREATE TABLE dota2bot.dota_match_players (
    friend_id BIGINT,
    match_id BIGINT,
    hero_id INTEGER NOT NULL,
    player_slot INTEGER NOT NULL,
    abandon BOOLEAN,
    PRIMARY KEY (friend_id, match_id),
    CONSTRAINT fk_match FOREIGN KEY (match_id) REFERENCES dota2bot.dota_matches (match_id) ON DELETE CASCADE
);

CREATE INDEX dota_match_players_match_id_idx ON dota2bot.dota_match_players (match_id);

CREATE TABLE dota2bot.dota_notable_players (
    friend_id BIGINT PRIMARY KEY,
    nickname TEXT -- Twitch name, pro player tag or nickname
);
