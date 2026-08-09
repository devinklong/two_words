-- Defines the ~150-player ownable pool: player-seasons where
-- mean_fantasy_score + k*stddev_fantasy_score >= threshold (ceiling-based
-- eligibility, NOT raw average — see methodology_notes.md's "Tobias
-- Harris problem" for why average-based selection was rejected).
--
-- k=1.25, threshold=35 chosen (8/8/26) from the Phase 1 sensitivity grid
-- (schema/threshold_sensitivity_grid.sql): at t=35, k=1.25 produced a
-- 37.3% clear_rate vs 49.2% at k=0.75 — the stricter/lower clear rate was
-- the preferred choice (fewer, higher-confidence locks over frequency).
-- k and threshold are deliberately kept as named constants below, not
-- buried in the WHERE clause, since both are still first-pass Phase 1
-- choices — Phase 2's train/test backtest (2021-24 / 2024-26) is the
-- planned mechanism to actually validate or revise them, not this file.
--
-- games_played >= 20 filter matches the same sample-size floor used in
-- player_variance_buckets, for consistency between the two player-season
-- level views this project now has.

DROP VIEW IF EXISTS ownable_player_pool;

CREATE VIEW ownable_player_pool AS
SELECT
    player_id,
    season_id,
    avg_fantasy_score,
    stddev_fantasy_score,
    ROUND(avg_fantasy_score + 1.25 * stddev_fantasy_score, 2) AS eligibility_ceiling
FROM player_season_fantasy_stats
WHERE games_played >= 20
  AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35;

-- =========================
-- Verification
-- =========================

-- Should land in the ballpark of ~150-210 player-seasons per season
-- (per the 8/4/26 pool-size discussion), not wildly higher/lower
SELECT season_id, COUNT(*) AS pool_size
FROM ownable_player_pool
GROUP BY season_id
ORDER BY season_id;

-- Spot check Jokić is in the pool every season (should trivially clear —
-- he's the target case for "auto-lock star" territory)
SELECT opp.*, p.full_name
FROM ownable_player_pool opp
JOIN players p ON p.player_id = opp.player_id
WHERE p.full_name ILIKE '%joki%'
ORDER BY opp.season_id;
