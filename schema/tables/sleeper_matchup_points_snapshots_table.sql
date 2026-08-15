-- sleeper_matchup_points_snapshots: Sleeper's own already-computed
-- aggregate points (points/starters_points/players_points) per
-- roster/week, captured as a CHANGE LOG -- a new row only when the
-- value differs from the last one recorded for that roster/week, not
-- one row per sync.
--
-- WHY THIS TABLE EXISTS AT ALL, despite the project's hard rule against
-- storing Sleeper's own points: confirmed 8/13/26 that Sleeper's live
-- API does not expose day-by-day lock history anywhere, even for
-- completed weeks -- /league/{id}/matchups/{week} only ever returns the
-- final roster snapshot + the point total already computed at the time.
-- For a daily-lineup league, that means the ONLY surviving record of
-- which player was actually locked in on which specific day is
-- whatever Sleeper had already computed before an overwrite. This
-- table exists to stop losing that the moment a lineup changes again.
--
-- ISOLATION, by design: this table is never joined into game_logs,
-- game_fantasy_scores, sleeper_scoring_constants, or anything the
-- lock/hold engine reads. These are aggregate, already-computed
-- values -- not an input to this project's own fantasy_score formula,
-- and not a result of scoring_settings either. Used only by
-- historical-standings/matchup-result reporting for weeks that have
-- already happened, where reproducing the real recorded outcome
-- matters more than independent verification.

DROP TABLE IF EXISTS sleeper_matchup_points_snapshots CASCADE;

CREATE TABLE sleeper_matchup_points_snapshots (
    league_id       TEXT        NOT NULL,
    week            INTEGER     NOT NULL,
    roster_id       INTEGER     NOT NULL,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    points          NUMERIC,
    starters_points NUMERIC[],
    players_points  JSONB,
    PRIMARY KEY (league_id, week, roster_id, synced_at)
);

-- =========================
-- Verification checks
-- =========================

-- Row count sanity -- should grow slowly over a live week (only on
-- real changes), not once per sync run
SELECT COUNT(*) AS total_snapshots FROM sleeper_matchup_points_snapshots;

-- Change history for one roster/week -- during a live week with a
-- mid-week swap, expect 2+ rows with different points values;
-- for a settled/no-change week, expect exactly 1
SELECT roster_id, week, synced_at, points
FROM sleeper_matchup_points_snapshots
WHERE league_id = '1214984705477185536'
ORDER BY roster_id, week, synced_at;

-- Confirm no duplicates snuck in — should be empty
SELECT league_id, week, roster_id, COUNT(*)
FROM sleeper_matchup_points_snapshots
GROUP BY league_id, week, roster_id
HAVING COUNT(*) > 1;

-- Most-recent snapshot per roster/week -- this is the value any
-- future historical-standings view should actually read
SELECT DISTINCT ON (league_id, week, roster_id)
    league_id, week, roster_id, synced_at, points
FROM sleeper_matchup_points_snapshots
ORDER BY league_id, week, roster_id, synced_at DESC;
