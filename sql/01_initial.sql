/* --- DOTA CONSTANTS ------------------------------------------------------------------------------------------------*/
CREATE TABLE
    /* Dota Items */
    IF NOT EXISTS shared.dota_constants_items (
        item_id INT PRIMARY KEY,
        display_name TEXT NOT NULL
    );

/* --- DOTA FRIENDS --------------------------------------------------------------------------------------------------*/
CREATE TABLE
    IF NOT EXISTS shared.dota_accounts (
        friend_id BIGINT PRIMARY KEY, -- steam32id (friend id) format;
        twitch_id TEXT NOT NULL REFERENCES irenesbot.tokens (user_id) ON DELETE CASCADE,
        last_seen TIMESTAMPTZ DEFAULT NOW ()
    );

CREATE TABLE
    IF NOT EXISTS shared.dota_matches (
        match_id BIGINT PRIMARY KEY,
        start_time TIMESTAMPTZ DEFAULT NOW (),
        lobby_type INT NOT NULL,
        game_mode INT NOT NULL,
        outcome INT DEFAULT (NULL),
        live INT DEFAULT (1),
        failed INT DEFAULT (0)
    );

CREATE TABLE
    IF NOT EXISTS shared.dota_match_players (
        friend_id BIGINT NOT NULL,
        match_id BIGINT NOT NULL,
        PRIMARY KEY (friend_id, match_id),
        hero_id INT NOT NULL,
        player_slot INT NOT NULL,
        abandon BOOLEAN DEFAULT (NULL),
        CONSTRAINT fk_match FOREIGN KEY (match_id) REFERENCES shared.dota_matches (match_id) ON DELETE CASCADE
    );

CREATE TABLE
    IF NOT EXISTS shared.dota_notable_players (
        friend_id BIGINT PRIMARY KEY,
        nickname TEXT -- either twitch name, pro player tag or nickname;
    );