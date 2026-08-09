-- Persists the season-relative quality tiering used throughout today's
-- analysis (analyze_ceiling_penalty_by_tier.py,
-- hold_value_by_tier_and_grw.sql) as a real view, so it's defined ONCE
-- and reused everywhere instead of copy-pasted CTEs drifting out of sync.
--
-- Tier boundaries (top 25 / 26-75 / 76+) match the diagnostic scripts
-- already run today. Restricted to the ownable pool (games_played>=20,
-- mean+1.25*stddev>=35), matching ownable_player_pool.sql exactly.
--
-- FIX (8/9/26): ROW_NUMBER() needs a deterministic tiebreaker. Without
-- one, two players tied (or near-tied under parallel query execution)
-- on avg_fantasy_score can get their rank/tier assignment flipped
-- between separate query runs of the SAME data -- found via
-- tests/lock_signal_validation.sql producing inconsistent player-season
-- samples across its own sections. player_id is stable and unique, so
-- it's used as the tiebreaker -- makes tier assignment fully
-- reproducible regardless of ties.

DROP VIEW IF EXISTS player_tiers;

CREATE VIEW player_tiers AS
WITH ranked_pool AS (
    SELECT
        player_id, season_id, avg_fantasy_score, stddev_fantasy_score,
        ROW_NUMBER() OVER (PARTITION BY season_id ORDER BY avg_fantasy_score DESC, player_id) AS rank_in_season
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35
)
SELECT
    player_id, season_id, avg_fantasy_score, stddev_fantasy_score, rank_in_season,
    CASE
        WHEN rank_in_season <= 25 THEN '1_elite'
        WHEN rank_in_season <= 75 THEN '2_mid'
        ELSE '3_lower'
    END AS tier
FROM ranked_pool;

SELECT tier, COUNT(*) FROM player_tiers GROUP BY tier ORDER BY tier;
