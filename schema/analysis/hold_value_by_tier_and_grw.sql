-- Tests whether the existing hold-value curve (percentage_to_lock, fit on
-- the OLD pooled/variance-bucketed population) still matches reality
-- under the NEW ceiling-based game_lock_signal -- segmented by player
-- tier (top 25 / 26-75 / 76+, same tiers as player_tiers) x
-- games_remaining_in_week (4/3/2/1 -- games_remaining=0 is excluded on
-- purpose: PASS always fires there by construction, HOLD never occurs at
-- grw=0, so there's nothing to compare).
--
-- IMPORTANT CONTEXT: percentage_to_lock does NOT currently drive any
-- decision in game_lock_signal -- it's exposed as data, not used in the
-- CASE logic. This query is diagnostic: it checks whether the curve is
-- still a trustworthy signal for the new tiered/ceiling-focused
-- population, which is a prerequisite for deciding whether it's worth
-- actually wiring in later, not a test of something already live.
--
-- DEDUPED 8/17/26 (docs/patch_list.md #3): pool/tier logic now reads
-- directly from player_tiers instead of re-deriving ranked_pool/
-- tiered_pool inline. player_tiers applies the identical pool-eligibility
-- filter (games_played >= 20, avg + 1.25*stddev >= 35) before ranking.

WITH future_scores AS (
    SELECT
        gls.player_id, gls.season_id, gls.week_number, gls.game_date,
        gls.games_remaining_in_week, gls.fantasy_score, gls.lock_signal,
        gls.percentage_to_lock, pt.tier,
        MAX(gls.fantasy_score) OVER (
            PARTITION BY gls.player_id, gls.season_id, gls.week_number
            ORDER BY gls.game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM game_lock_signal gls
    JOIN player_tiers pt ON pt.player_id = gls.player_id AND pt.season_id = gls.season_id
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

WITH old_pool AS (
    SELECT player_id, season_id
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35
)
SELECT COUNT(*) AS mismatches
FROM old_pool op
FULL OUTER JOIN player_tiers pt
    ON pt.player_id = op.player_id AND pt.season_id = op.season_id
WHERE op.player_id IS NULL OR pt.player_id IS NULL;
