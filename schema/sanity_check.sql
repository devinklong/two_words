-- Sanity check: confirms tonight's fixes are actually holding. Run this
-- any time you want to verify the pipeline hasn't silently reverted,
-- without re-reading a wall of psql output.

-- 1. Both materialized views should show relkind = 'm'
SELECT relname, relkind
FROM pg_class
WHERE relname IN ('game_fantasy_scores_weekly_effective', 'team_schedule_b2b_flags');
-- EXPECT: 2 rows, both relkind = 'm'

-- 2. Row counts should match their source tables exactly
SELECT
    (SELECT COUNT(*) FROM team_schedule_b2b_flags) AS b2b_flags_count,
    (SELECT COUNT(*) FROM team_schedule) AS team_schedule_count,
    (SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective) AS effective_count;
-- EXPECT: b2b_flags_count = team_schedule_count (12,300)
--         effective_count = 122,569

-- 3. The actual hold-value query — should return in well under 1 second
WITH future_scores AS (
    SELECT
        gfsw.games_remaining_in_week,
        gfsw.fantasy_score,
        MAX(gfsw.fantasy_score) OVER (
            PARTITION BY gfsw.player_id, gfsw.season_id, gfsw.week_number
            ORDER BY gfsw.game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM game_fantasy_scores_weekly_effective gfsw
)
SELECT
    games_remaining_in_week,
    COUNT(*) AS decision_points,
    ROUND(100.0 * SUM((best_remaining_score > fantasy_score)::INT) / COUNT(*), 1) AS hold_wins_pct
FROM future_scores
WHERE games_remaining_in_week >= 1
GROUP BY games_remaining_in_week
ORDER BY games_remaining_in_week;
-- EXPECT: grw=1 -> 42.0%, grw=2 -> 58.6%, grw=3 -> 66.6%, grw=4 -> 70.2%
