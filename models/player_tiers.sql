-- Season-relative quality tiers within the ownable pool (top 25 = elite,
-- 26-75 = mid, 76+ = lower), defined once and reused everywhere tiering
-- is needed instead of copy-pasted CTEs drifting out of sync.

DROP VIEW IF EXISTS player_tiers;

CREATE VIEW player_tiers AS
WITH ranked_pool AS (
    SELECT
        player_id, season_id, avg_fantasy_score, stddev_fantasy_score,
        -- player_id tiebreaker: without it, ties on avg_fantasy_score can
        -- flip rank/tier assignment between separate runs of the same data
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
