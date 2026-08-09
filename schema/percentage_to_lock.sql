-- BUCKETED VERSION (8/8/26) — replaces the single pooled curve with two
-- curves, one per variance tier, so percentage_to_lock reflects the
-- specific player's own volatility rather than a league-wide average.
--
-- WHY BUCKETED, NOT FULLY PER-PLAYER: a true per-player curve needs
-- enough of THAT player's own decision points at each games_remaining
-- level to fit 2 parameters — most players only generate ~15-20 weeks of
-- data a season, spread across 4 games_remaining levels, nowhere near
-- enough (compare to the 37,046 decision points the pooled grw=1 curve
-- used). Bucketing by variance tier is the smallest change that's still
-- statistically honest: each bucket still pools thousands of decision
-- points, but a high-variance/streamer-type player and a low-variance/
-- consistent player get genuinely different curves, matching the real
-- intuition that a boom-or-bust player is more likely to have a swing
-- game later in the week than a steady one.
--
-- player_variance_buckets: NTILE(2) split on stddev_fantasy_score,
-- PARTITIONed by season (a player's role/variance can shift year to
-- year), restricted to games_played >= 20 (same sample-size floor used
-- in earlier eyeball checks) so the split isn't distorted by tiny
-- samples. bucket 1 = lower stddev half (more consistent), bucket 2 =
-- higher stddev half (streakier).
--
-- hold_value_curve_params: stores the fitted a/b PER BUCKET. Populated
-- by scripts/fit_hold_value_curve.py, which now UPSERTs directly into
-- this table instead of printing constants to hand-paste — safe to
-- rerun every season, no SQL editing required.
--
-- SETUP ORDER: run this file first (creates the view/table/function
-- with an EMPTY params table), then run
-- scripts/fit_hold_value_curve.py to populate hold_value_curve_params.
-- Until the params table has rows, hold_win_probability() will raise an
-- exception rather than silently return a wrong number.

DROP VIEW IF EXISTS player_variance_buckets;

CREATE VIEW player_variance_buckets AS
SELECT
    player_id,
    season_id,
    NTILE(2) OVER (PARTITION BY season_id ORDER BY stddev_fantasy_score) AS variance_bucket
FROM player_season_fantasy_stats
WHERE games_played >= 20;

CREATE TABLE IF NOT EXISTS hold_value_curve_params (
    variance_bucket INTEGER PRIMARY KEY,
    a               NUMERIC NOT NULL,
    b               NUMERIC NOT NULL,
    fitted_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION hold_win_probability(games_remaining BIGINT, bucket INTEGER)
RETURNS NUMERIC AS $$
DECLARE
    a_val NUMERIC;
    b_val NUMERIC;
BEGIN
    IF games_remaining <= 0 THEN
        RETURN 0;  -- no future games this week -> zero chance of a later win
    END IF;

    SELECT a, b INTO a_val, b_val
    FROM hold_value_curve_params
    WHERE variance_bucket = bucket;

    IF a_val IS NULL THEN
        RAISE EXCEPTION 'No fitted curve for variance_bucket=%. Run scripts/fit_hold_value_curve.py to populate hold_value_curve_params.', bucket;
    END IF;

    RETURN a_val * (1 - POWER(1 - b_val, games_remaining));
END;
$$ LANGUAGE plpgsql STABLE;
-- STABLE (not IMMUTABLE) since it now reads hold_value_curve_params.

DROP VIEW IF EXISTS game_fantasy_scores_weekly_lock_signal;

CREATE VIEW game_fantasy_scores_weekly_lock_signal AS
SELECT
    gfsw.*,
    COALESCE(pvb.variance_bucket, 1) AS variance_bucket,  -- players below the
        -- games_played >= 20 floor (e.g. mid-season call-ups) fall back to
        -- bucket 1 (conservative/low-variance curve) rather than erroring —
        -- a simplification worth revisiting if this pool matters for v1.1
    ROUND(1 - hold_win_probability(
        gfsw.games_remaining_in_week,
        COALESCE(pvb.variance_bucket, 1)
    ), 4) AS percentage_to_lock
FROM game_fantasy_scores_weekly_effective gfsw
LEFT JOIN player_variance_buckets pvb
    ON pvb.player_id = gfsw.player_id AND pvb.season_id = gfsw.season_id;

-- =========================
-- Verification
-- =========================
-- NOTE: these will ERROR until hold_value_curve_params has been populated
-- by scripts/fit_hold_value_curve.py — that's expected, not a bug.

-- Row count should still match exactly — only adding columns
SELECT COUNT(*) FROM game_fantasy_scores_weekly_lock_signal;
SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective;

-- Bucket sizes — should be roughly even per season (NTILE(2) splits as
-- evenly as possible)
SELECT season_id, variance_bucket, COUNT(*) AS player_count
FROM player_variance_buckets
GROUP BY season_id, variance_bucket
ORDER BY season_id, variance_bucket;

-- Last game of the week should always be percentage_to_lock = 1.0000,
-- regardless of bucket
SELECT COUNT(*) AS violations
FROM game_fantasy_scores_weekly_lock_signal
WHERE is_last_game_of_week AND percentage_to_lock != 1.0000;

-- Sanity check each bucket's curve against its own actual empirical
-- values — compare this to scripts/fit_hold_value_curve.py's printed
-- report for each bucket, they should match closely
SELECT
    variance_bucket,
    games_remaining_in_week,
    COUNT(*) AS decision_points,
    ROUND(100 * (1 - AVG(percentage_to_lock)), 1) AS implied_hold_wins_pct_from_curve
FROM game_fantasy_scores_weekly_lock_signal
WHERE games_remaining_in_week >= 1
GROUP BY variance_bucket, games_remaining_in_week
ORDER BY variance_bucket, games_remaining_in_week;

-- Spot check Jokić's week 5, 2024-25 — which bucket did he land in, and
-- does his percentage_to_lock differ from the old pooled-curve version?
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.variance_bucket, gfsw.fantasy_score, gfsw.percentage_to_lock
FROM game_fantasy_scores_weekly_lock_signal gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
