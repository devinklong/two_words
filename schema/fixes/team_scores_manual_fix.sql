-- schema/fixes/team_scores_manual_fix.sql
--
-- Manually re-verified team_scores corrections, confirmed against the
-- real Sleeper app 8/23/26 -- the final piece of this session's full
-- player_scores/team_scores verification pass (see
-- verify_team_scores_against_xlsx.py's original 23-row mismatch
-- output). All 21 rows checked directly against the app: the xlsx was
-- correct in every case, the DB was wrong. 3 rows (2024-25) were
-- pre-existing unexplained anomalies from earlier this session; the
-- other 18 (2025-26, weeks 21-22) are a real, isolated recurrence of
-- the same never-fully-root-caused Sleeper live-API instability from
-- Step 6 (6 tested theories, no confirmed cause) -- localized to the
-- regular-season/playoff transition boundary, not spread season-wide.
--
-- sleeper_matchup_points_snapshots is append-only + change-detecting
-- (same convention as backfill_manual_team_points.py) -- these INSERTs
-- add new rows that become each key's "latest" snapshot without
-- deleting or overwriting the incorrect prior rows, same safe pattern
-- used for every other manual correction this session.
--
-- Run once. Safe to re-run (duplicate inserts just add redundant
-- identical "latest" rows, doesn't change the resolved value) but not
-- necessary to.

INSERT INTO sleeper_matchup_points_snapshots (league_id, week, roster_id, points) VALUES
('1113487058661744640', 14, 7, 447.25),
('1113487058661744640', 15, 6, 379.8),
('1113487058661744640', 20, 5, 396.8),
('1214984705477185536', 21, 1,  392.7),
('1214984705477185536', 21, 2,  398.9),
('1214984705477185536', 21, 3,  404.7),
('1214984705477185536', 21, 4,  157.3),
('1214984705477185536', 21, 5,  419.0),
('1214984705477185536', 21, 6,  258.55),
('1214984705477185536', 21, 7,  475.7),
('1214984705477185536', 21, 8,  460.75),
('1214984705477185536', 21, 9,  439.7),
('1214984705477185536', 21, 10, 359.05),
('1214984705477185536', 22, 1,  298.8),
('1214984705477185536', 22, 2,  447.60),
('1214984705477185536', 22, 3,  325.40),
('1214984705477185536', 22, 4,  248.40),
('1214984705477185536', 22, 5,  435.8),
('1214984705477185536', 22, 6,  388.90),
('1214984705477185536', 22, 7,  373.65),
('1214984705477185536', 22, 10, 296.35);

-- =========================
-- Verification
-- =========================

-- Should return 21 -- one row per correction just inserted
SELECT COUNT(*) AS rows_just_inserted
FROM sleeper_matchup_points_snapshots
WHERE (league_id = '1113487058661744640' AND week IN (14, 15, 20))
   OR (league_id = '1214984705477185536' AND week IN (21, 22));

-- Confirm each key's LATEST snapshot now matches the corrected value
-- (dedup logic matches sleeper_matchup_points_latest / this session's
-- verify_team_scores_against_xlsx.py)
SELECT DISTINCT ON (league_id, week, roster_id)
    league_id, week, roster_id, points, synced_at
FROM sleeper_matchup_points_snapshots
WHERE (league_id = '1113487058661744640' AND week IN (14, 15, 20))
   OR (league_id = '1214984705477185536' AND week IN (21, 22))
ORDER BY league_id, week, roster_id, synced_at DESC;

-- Real final check: rerun scripts/verify_team_scores_against_xlsx.py
-- against 2024_2025_all_scores.xlsx -- expect 0 mismatches.
