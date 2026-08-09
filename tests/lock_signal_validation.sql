-- Manual validation sample for game_lock_signal (8/8/26): 15 players per
-- season, sampled at rank positions 1, 11, 21, 31, ..., 141 (every 10th
-- rank, starting at 1) by avg_fantasy_score DESC, across all 5 backfilled
-- seasons (2021-22 through 2025-26). Up to 75 player-seasons total,
-- spanning the full quality spectrum from top stars down through players
-- well outside the ~150-210 ownable pool — deliberately broader than the
-- pool itself, so this also validates the pool BOUNDARY (rank 141 by
-- average is not the same thing as pool eligibility, since the pool uses
-- mean + 1.25*stddev, not raw average — see ownable_player_pool.sql). A
-- sampled player showing 0 rows in section 2/3 below is expected whenever
-- they didn't clear the ceiling-based pool threshold, not a bug.
--
-- This is a manual-review script, not an automated pass/fail test suite
-- — eyeball the output for anything that looks wrong (a low-average
-- player showing mostly LOCK, a star showing mostly PASS, an empty
-- pool-member row, etc), same spot-check pattern as the earlier Jokić/
-- Jaquez checks, just at 15x the coverage and across all 5 seasons
-- instead of one.

-- =========================
-- 1. The sample roster itself, with pool membership flagged
-- =========================

WITH ranked_players AS (
    SELECT
        pss.player_id,
        pss.season_id,
        pss.full_name,
        pss.games_played,
        pss.avg_fantasy_score,
        pss.stddev_fantasy_score,
        ROW_NUMBER() OVER (
            PARTITION BY pss.season_id ORDER BY pss.avg_fantasy_score DESC
        ) AS rank_by_avg_score
    FROM player_season_fantasy_stats pss
)
SELECT
    rp.season_id,
    rp.rank_by_avg_score,
    rp.full_name,
    rp.games_played,
    rp.avg_fantasy_score,
    rp.stddev_fantasy_score,
    (opp.player_id IS NOT NULL) AS in_ownable_pool
FROM ranked_players rp
LEFT JOIN ownable_player_pool opp
    ON opp.player_id = rp.player_id AND opp.season_id = rp.season_id
WHERE rp.season_id IN ('22021', '22022', '22023', '22024', '22025')
  AND rp.rank_by_avg_score IN (1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101, 111, 121, 131, 141)
ORDER BY rp.season_id, rp.rank_by_avg_score;

-- =========================
-- 2. Per-player-season LOCK/HOLD/PASS summary (pool members only — non-
-- pool sampled players correctly produce zero rows here, see note above)
-- =========================

WITH ranked_players AS (
    SELECT
        pss.player_id,
        pss.season_id,
        pss.full_name,
        ROW_NUMBER() OVER (
            PARTITION BY pss.season_id ORDER BY pss.avg_fantasy_score DESC
        ) AS rank_by_avg_score
    FROM player_season_fantasy_stats pss
),
sample_players AS (
    SELECT player_id, season_id, full_name, rank_by_avg_score
    FROM ranked_players
    WHERE season_id IN ('22021', '22022', '22023', '22024', '22025')
      AND rank_by_avg_score IN (1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101, 111, 121, 131, 141)
)
SELECT
    sp.season_id,
    sp.rank_by_avg_score,
    sp.full_name,
    gls.lock_signal,
    COUNT(*) AS game_count
FROM sample_players sp
JOIN game_lock_signal gls
    ON gls.player_id = sp.player_id AND gls.season_id = sp.season_id
GROUP BY sp.season_id, sp.rank_by_avg_score, sp.full_name, gls.lock_signal
ORDER BY sp.season_id, sp.rank_by_avg_score, gls.lock_signal;

-- =========================
-- 3. Full row-level detail for the sample, for manual eyeball review
-- =========================

WITH ranked_players AS (
    SELECT
        pss.player_id,
        pss.season_id,
        pss.full_name,
        ROW_NUMBER() OVER (
            PARTITION BY pss.season_id ORDER BY pss.avg_fantasy_score DESC
        ) AS rank_by_avg_score
    FROM player_season_fantasy_stats pss
),
sample_players AS (
    SELECT player_id, season_id, full_name, rank_by_avg_score
    FROM ranked_players
    WHERE season_id IN ('22021', '22022', '22023', '22024', '22025')
      AND rank_by_avg_score IN (1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101, 111, 121, 131, 141)
)
SELECT
    sp.season_id,
    sp.rank_by_avg_score,
    sp.full_name,
    gls.game_date,
    gls.week_number,
    gls.games_remaining_in_week,
    gls.variance_bucket,
    gls.fantasy_score,
    gls.percentage_to_lock,
    gls.lock_signal
FROM sample_players sp
JOIN game_lock_signal gls
    ON gls.player_id = sp.player_id AND gls.season_id = sp.season_id
ORDER BY sp.season_id, sp.rank_by_avg_score, gls.game_date;
