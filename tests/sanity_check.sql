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

-- 4. Bucketed lock signal — condensed to ONE LINE PER BUCKET (each
-- bucket's whole curve packed into a single text column) instead of a
-- wide games_remaining x bucket grid, specifically so it doesn't wrap in
-- a terminal. Copy/paste-friendly for sharing results.

-- 4a. Confirms the fit script actually ran and populated both buckets
SELECT variance_bucket, a, b, fitted_at FROM hold_value_curve_params ORDER BY variance_bucket;
-- EXPECT: 2 rows (bucket 1 and 2). Zero rows means
-- scripts/fit_hold_value_curve.py hasn't been run yet.

-- 4b. Each bucket's whole curve on one line
SELECT
    variance_bucket,
    STRING_AGG(
        games_remaining_in_week || ':' || ROUND(100 * (1 - avg_lock), 1) || '%',
        ', ' ORDER BY games_remaining_in_week
    ) AS curve_summary  -- e.g. "1:41.6%, 2:59.2%, 3:66.7%, 4:69.9%"
FROM (
    SELECT variance_bucket, games_remaining_in_week, AVG(percentage_to_lock) AS avg_lock
    FROM game_fantasy_scores_weekly_lock_signal
    WHERE games_remaining_in_week >= 1
    GROUP BY variance_bucket, games_remaining_in_week
) sub
GROUP BY variance_bucket
ORDER BY variance_bucket;
-- EXPECT: 2 rows, curve_summary showing hold_wins_pct climbing with
-- games_remaining for each bucket. Bucket 2 (streakier) should generally
-- look meaningfully different from bucket 1 (steadier) -- if the two
-- curves are nearly identical, the bucketing isn't adding real signal
-- over the earlier pooled version.
