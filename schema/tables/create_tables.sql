-- Single source of truth for "what season is currently live" -- read
-- by ownable_player_pool.sql's bootstrap CTE (SQL) AND
-- scripts/constants.py's get_current_season_id() (Python), so the two
-- can never drift out of sync. Same pattern already established by
-- sleeper_scoring_constants -- a real operational fact lives in the
-- DB, not hardcoded per-file/per-language.
--
-- >>> UPDATE THIS ROW AT THE START OF EACH NEW SEASON: <
--   UPDATE current_season_config SET season_id = '22027', updated_at = now() WHERE id = 1;

-- Safe to re-run: no DROP. The old DROP ... CASCADE also dropped ownable_player_pool, game_lock_signal and nba_player_movers, and reset
-- the season to 22026 mid-season.
CREATE TABLE IF NOT EXISTS current_season_config (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    season_id  TEXT NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT single_row CHECK (id = 1)
);

INSERT INTO current_season_config (id, season_id) VALUES (1, '22026') ON CONFLICT (id) DO NOTHING;

CREATE TABLE IF NOT EXISTS teams (
    team_id INTEGER PRIMARY KEY,
    abbreviation VARCHAR(3) NOT NULL,
    full_name VARCHAR(50) NOT NULL,
    city VARCHAR(50),
    state VARCHAR(50),
    year_founded INTEGER
);

CREATE TABLE IF NOT EXISTS nba_players (
    player_id INTEGER PRIMARY KEY,
    full_name VARCHAR(100) NOT NULL,
    first_name VARCHAR(50),
    last_name VARCHAR(50),
    is_active BOOLEAN
);