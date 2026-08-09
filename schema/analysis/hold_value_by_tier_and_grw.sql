-- Tests whether the existing hold-value curve (percentage_to_lock, fit on
-- the OLD pooled/variance-bucketed population) still matches reality
-- under the NEW ceiling-based game_lock_signal -- segmented by player
-- tier (top 25 / 26-75 / 76+, same tiers as
-- analyze_ceiling_penalty_by_tier.py) x games_remaining_in_week (4/3/2/1
-- -- games_remaining=0 is excluded on purpose: PASS always fires there
-- by construction, HOLD never occurs at grw=0, so there's nothing to
-- compare).
--
-- IMPORTANT CONTEXT: percentage_to_lock does NOT currently drive any
-- decision in game_lock_signal -- it's exposed as data, not used in the
-- CASE logic. This query is diagnostic: it checks whether the curve is
-- still a trustworthy signal for the new tiered/ceiling-focused
-- population, which is a prerequisite for deciding whether it's worth
-- actually wiring in later, not a test of something already live.

WITH tiered_pool AS (
    SELECT
        player_id, season_id,
        ROW_NUMBER() OVER (PARTITION BY season_id ORDER BY avg_fantasy_score DESC, player_id) AS rank_in_season
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35
),
tier_labeled AS (
    SELECT
        *,
        CASE
            WHEN rank_in_season <= 25 THEN '1_elite (top 25)'
            WHEN rank_in_season <= 75 THEN '2_mid (26-75)'
            ELSE '3_lower (76+)'
        END AS tier
    FROM tiered_pool
),
future_scores AS (
    SELECT
        gls.player_id, gls.season_id, gls.week_number, gls.game_date,
        gls.games_remaining_in_week, gls.fantasy_score, gls.lock_signal,
        gls.percentage_to_lock, tl.tier,
        MAX(gls.fantasy_score) OVER (
            PARTITION BY gls.player_id, gls.season_id, gls.week_number
            ORDER BY gls.game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM game_lock_signal gls
    JOIN tier_labeled tl ON tl.player_id = gls.player_id AND tl.season_id = gls.season_id
)
SELECT
    tier,
    games_remaining_in_week,
    COUNT(*) AS hold_decision_points,
    ROUND(100.0 * SUM((best_remaining_score > fantasy_score)::INT) / COUNT(*), 1) AS actual_hold_wins_pct,
    ROUND(100.0 * AVG(1 - percentage_to_lock), 1) AS curve_predicted_hold_wins_pct,
    ROUND(
        100.0 * SUM((best_remaining_score > fantasy_score)::INT) / COUNT(*)
        - 100.0 * AVG(1 - percentage_to_lock), 1
    ) AS actual_minus_predicted
FROM future_scores
WHERE lock_signal = 'HOLD' AND games_remaining_in_week BETWEEN 1 AND 4
GROUP BY tier, games_remaining_in_week
ORDER BY tier, games_remaining_in_week;
