-- Links Sleeper's player_id scheme to this project's existing player
-- identity. Sleeper's cross-reference IDs (espn_id, sportradar_id,
-- yahoo_id, stats_id) are NOT confirmed to equal nba_api's PERSON_ID --
-- matching is name-based (see build_sleeper_player_crosswalk.py),
-- with those IDs kept in sleeper_metadata as reference data only.

DROP TABLE IF EXISTS sleeper_player_crosswalk;

CREATE TABLE sleeper_player_crosswalk (
    sleeper_player_id  TEXT    PRIMARY KEY,
    nba_player_id      INTEGER UNIQUE REFERENCES players(player_id),
    sleeper_full_name  TEXT,
    sleeper_team        TEXT,
    sleeper_position    TEXT,
    match_method        TEXT    NOT NULL,  -- 'exact_name', 'manual'
    sleeper_metadata     JSONB,             -- raw espn_id/sportradar_id/yahoo_id/stats_id etc, unused for matching
    matched_at           TIMESTAMP NOT NULL DEFAULT now()
);

INSERT INTO sleeper_player_crosswalk
    (sleeper_player_id, nba_player_id, sleeper_full_name, match_method)
VALUES ('2397', 1630314, 'Brandon Williams', 'manual');

-- =========================
-- Verification
-- =========================
SELECT match_method, COUNT(*) FROM sleeper_player_crosswalk GROUP BY match_method;
SELECT COUNT(*) AS unmatched_nba_player_id FROM sleeper_player_crosswalk WHERE nba_player_id IS NULL;
-- unmatched rows need manual review before anything joins through them.
