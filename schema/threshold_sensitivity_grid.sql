-- Phase 1 diagnostic: for a grid of candidate (stdev_multiplier, lock_threshold)
-- pairs, reports two things:
--   1. eligible_pool_size — how many player-seasons qualify as "coveted"
--      (mean + k*stddev >= threshold) under that combo
--   2. clear_rate — of THOSE eligible players' individual games, what
--      fraction actually clear the threshold in a given game
--
-- Interpretation: clear_rate near 100% means the threshold is too low to
-- meaningfully separate "lockable" from "not yet" for that pool — almost
-- everything qualifies, so the bar isn't doing real work. clear_rate near
-- 0% means the bar is too strict even for players judged capable of hitting
-- it. Somewhere in between is where a threshold is actually discriminating
-- between games worth locking and games worth holding for something better
-- — that's the range worth carrying into the real Phase 2 backtest.
--
-- This is NOT the final answer — it's a map of the tradeoff space to narrow
-- down what's worth testing properly in a real weekly-outcome simulation.

WITH candidate_grid AS (
    SELECT k, t
    FROM UNNEST(ARRAY[0.75, 1.0, 1.25]) AS k
    CROSS JOIN UNNEST(ARRAY[35, 38, 40, 42, 45, 48]) AS t
),
eligible_pools AS (
    SELECT
        g.k,
        g.t,
        pss.player_id,
        pss.season_id
    FROM candidate_grid g
    JOIN player_season_fantasy_stats pss
        ON pss.avg_fantasy_score + (g.k * pss.stddev_fantasy_score) >= g.t
    WHERE pss.games_played >= 20  -- filter tiny samples, same as earlier eyeball check
)
SELECT
    ep.k AS stdev_multiplier,
    ep.t AS lock_threshold,
    COUNT(DISTINCT ep.player_id || '-' || ep.season_id) AS eligible_pool_size,
    COUNT(gfs.fantasy_score) AS eligible_pool_total_games,
    SUM((gfs.fantasy_score >= ep.t)::INT) AS games_clearing_threshold,
    ROUND(
        100.0 * SUM((gfs.fantasy_score >= ep.t)::INT) / NULLIF(COUNT(gfs.fantasy_score), 0),
        1
    ) AS clear_rate_pct
FROM eligible_pools ep
JOIN game_fantasy_scores gfs
    ON gfs.player_id = ep.player_id AND gfs.season_id = ep.season_id
GROUP BY ep.k, ep.t
ORDER BY ep.k, ep.t;
