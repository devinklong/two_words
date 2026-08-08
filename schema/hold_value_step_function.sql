-- For every decision point with at least 1 game remaining that week,
-- finds the BEST score the player actually produced later in the same
-- week, and compares it to the current game's score. Grouped by raw
-- games_remaining_in_week — no assumed curve shape, just the real
-- empirical relationship between "games left" and "was holding actually
-- worth it, in hindsight."
--
-- hold_wins_pct: of all decision points at this games-remaining level,
-- what % of the time did a LATER game that week score higher?
-- avg_score_delta_if_hold: on average, by how much? (can be negative
-- overall even if hold_wins_pct is meaningful, since losses when holding
-- doesn't pay off need to be weighed against wins when it does)
--
-- BUG FIX (8/8/26): the games_remaining_in_week >= 1 filter was
-- previously applied INSIDE the future_scores CTE, before the window
-- function ran. That stripped every is_last_game_of_week row (grw = 0)
-- out of the partition before it could be used as a "future" candidate,
-- so any decision point whose only remaining game was the last game of
-- the week had nothing left to compare against — this silently zeroed
-- out grw=1 results (0/37,046 wins, impossible for real NBA data).
-- Fix: compute the window over the FULL week (no filter), then filter
-- to games_remaining_in_week >= 1 only in the final SELECT, after
-- best_remaining_score has already been correctly populated using the
-- full week including its grw=0 row.

WITH future_scores AS (
    SELECT
        gfsw.player_id,
        gfsw.season_id,
        gfsw.week_number,
        gfsw.game_date,
        gfsw.games_remaining_in_week,
        gfsw.fantasy_score,
        MAX(gfsw.fantasy_score) OVER (
            PARTITION BY gfsw.player_id, gfsw.season_id, gfsw.week_number
            ORDER BY gfsw.game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM game_fantasy_scores_weekly_effective gfsw
    -- NOTE: no WHERE clause here. The window needs every game in the
    -- week, including the grw=0 (last game of week) row, to correctly
    -- serve as the "future" comparison point for earlier decision points.
)

SELECT
    games_remaining_in_week,
    COUNT(*) AS decision_points,
    ROUND(AVG(fantasy_score), 2) AS avg_current_score,
    ROUND(AVG(best_remaining_score), 2) AS avg_best_remaining_score,
    SUM((best_remaining_score > fantasy_score)::INT) AS times_holding_wouldve_won,
    ROUND(
        100.0 * SUM((best_remaining_score > fantasy_score)::INT) / COUNT(*),
        1
    ) AS hold_wins_pct,
    ROUND(AVG(best_remaining_score - fantasy_score), 2) AS avg_score_delta_if_hold
FROM future_scores
WHERE games_remaining_in_week >= 1
GROUP BY games_remaining_in_week
ORDER BY games_remaining_in_week;
