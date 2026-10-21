-- players: pure identity data only (per project decision — no team_id,
-- since team affiliation is time-varying and lives in game_logs/team_schedule).
-- CASCADE drops the FK constraint on game_logs.player_id if it exists;
-- it does NOT drop game_logs itself.

DROP TABLE IF EXISTS nba_players;

CREATE TABLE nba_players (
    player_id   INTEGER      PRIMARY KEY,   -- nba_api's id / PERSON_ID
    full_name   VARCHAR(100) NOT NULL,
    first_name  VARCHAR(50)  NOT NULL,
    last_name   VARCHAR(50)  NOT NULL,
    is_active   BOOLEAN      NOT NULL
);

-- =========================
-- Verification checks — run after load_players.py populates this table
-- =========================

-- Row count sanity check (active roster is currently a few hundred players)
SELECT COUNT(*) AS total_players FROM nba_players;

SELECT COUNT(*) AS active_players FROM nba_players WHERE is_active = TRUE;

-- No nulls should exist in required columns
SELECT COUNT(*) AS null_check
FROM nba_players
WHERE player_id IS NULL OR full_name IS NULL OR is_active IS NULL;

-- player_id is the PK, but double-check no duplicates slipped in pre-insert
SELECT player_id, COUNT(*)
FROM nba_players
GROUP BY player_id
HAVING COUNT(*) > 1;

-- Spot check a known player
SELECT * FROM nba_players WHERE full_name ILIKE '%raynaud%';

-- Eyeball a sample
SELECT * FROM nba_players ORDER BY full_name LIMIT 10;
