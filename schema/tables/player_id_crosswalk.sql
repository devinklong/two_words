-- Maps nba_api's player_id (canonical across the whole schema) to Sleeper's
-- own player_id, so Sleeper-side tables (rosters, transactions, matchups)
-- can join back to real stats via the same player_id every other view uses.
-- NOT YET IN USE: this is schema only, stays empty until the Sleeper
-- pipeline exists and a weekly sync script populates it -- building the
-- nba_api daily pipeline is the higher priority; this can wait.

CREATE TABLE player_id_crosswalk (
    player_id          INTEGER PRIMARY KEY REFERENCES players(player_id),
    sleeper_player_id  TEXT NOT NULL UNIQUE,
    match_method        TEXT,        -- e.g. 'exact_name', 'manual'
    matched_at          TIMESTAMP NOT NULL DEFAULT now()
);

-- Weekly sync upsert pattern (for the future matching script to use, one
-- row per Sleeper player processed): keyed on player_id, not
-- sleeper_player_id, so a manual correction or a Sleeper-side ID change
-- self-heals on the next run instead of silently no-op'ing.
--
-- INSERT INTO player_id_crosswalk (player_id, sleeper_player_id, match_method)
-- VALUES (%s, %s, %s)
-- ON CONFLICT (player_id)
-- DO UPDATE SET sleeper_player_id = EXCLUDED.sleeper_player_id,
--               match_method = EXCLUDED.match_method,
--               matched_at = now();
