-- Defines the ~150-210 player-season "ownable pool": mean + 1.25*stddev >= 35
-- (ceiling-based, not raw average, so spike-capable players aren't missed —
-- see methodology_notes.md's "Tobias Harris problem"). k and threshold are
-- kept as literals here deliberately, not buried, since they're first-pass
-- values Phase 2's backtest is the mechanism to revise, not this file.

DROP VIEW IF EXISTS ownable_player_pool;

CREATE VIEW ownable_player_pool AS
SELECT
    player_id,
    season_id,
    avg_fantasy_score,
    stddev_fantasy_score,
    ROUND(avg_fantasy_score + 1.25 * stddev_fantasy_score, 2) AS eligibility_ceiling
FROM player_season_fantasy_stats
WHERE games_played >= 20  -- same sample-size floor used across the schema
  AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35;

-- =========================
-- Verification
-- =========================

-- Expect ~150-210 per season
SELECT season_id, COUNT(*) AS pool_size
FROM ownable_player_pool
GROUP BY season_id
ORDER BY season_id;

-- Jokić should trivially clear every season
SELECT opp.*, p.full_name
FROM ownable_player_pool opp
JOIN players p ON p.player_id = opp.player_id
WHERE p.full_name ILIKE '%joki%'
ORDER BY opp.season_id;
