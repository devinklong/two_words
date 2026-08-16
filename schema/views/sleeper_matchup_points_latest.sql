-- sleeper_matchup_points_latest: dedupes sleeper_matchup_points_snapshots
-- down to the single most-recent snapshot per (league_id, week,
-- roster_id) -- during a live season with mid-week swaps there can be
-- multiple snapshots per roster/week; this is always "whatever the
-- final recorded value was."
--
-- Confirmed missing from the live DB 8/15/26 -- historical_matchup_
-- results_view.sql references this view directly but it was never
-- actually created (referenced in the file's own header comment as
-- "sleeper_matchup_points_current," but the real query uses this name
-- instead -- the CREATE VIEW statement for it was simply never written).
-- Same dedup logic verify_matchup_points_independently.py already
-- reimplements inline for its own independent check.

DROP VIEW IF EXISTS sleeper_matchup_points_latest;

CREATE VIEW sleeper_matchup_points_latest AS
SELECT DISTINCT ON (league_id, week, roster_id)
    league_id,
    week,
    roster_id,
    points,
    starters_points,
    players_points,
    synced_at
FROM sleeper_matchup_points_snapshots
ORDER BY league_id, week, roster_id, synced_at DESC;

-- =========================
-- Verification
-- =========================

-- One row per (league_id, week, roster_id) that has at least one
-- snapshot -- no duplicates should be possible given DISTINCT ON
SELECT league_id, week, roster_id, COUNT(*)
FROM sleeper_matchup_points_latest
GROUP BY league_id, week, roster_id
HAVING COUNT(*) > 1;
-- EXPECT: 0 rows

SELECT COUNT(*) AS total_latest_rows FROM sleeper_matchup_points_latest;

-- Spot check one roster/week that had a mid-week swap, if any exist --
-- confirms this is actually picking the LATEST synced_at, not just any row
SELECT league_id, week, roster_id, points, synced_at
FROM sleeper_matchup_points_latest
ORDER BY league_id, week, roster_id
LIMIT 20;
