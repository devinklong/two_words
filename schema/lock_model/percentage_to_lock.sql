-- REDESIGNED (8/9/26) -- REPLACES the variance-bucket version.
--
-- Old version's curve was fit on the full, unconditioned population
-- (every game, regardless of whether it already cleared that player's
-- own bar) and used variance_bucket (2 groups) for segmentation. Found
-- empirically (hold_value_by_tier_and_grw.sql) it systematically
-- UNDER-predicted real hold value by 10-16pp once restricted to the
-- population it's actually applied to under game_lock_signal's
-- ceiling-based design (fantasy_score < player's own lock_bar). Refit
-- directly on that conditional population instead, segmented by the
-- same tier (top 25 / 26-75 / 76+, player_tiers.sql) used everywhere
-- else in today's analysis, replacing the old 2-bucket variance split.
--
-- SETUP ORDER: run this file first (empty params table), then
-- scripts/fit_hold_value_curve_by_tier.py to populate
-- hold_value_curve_params_by_tier.
--
-- PURPOSE (per user, 8/9/26): this is the actual crux input for a
-- planned real-time tool -- enter a completed game's stat line, compute
-- fantasy_score from the scoring formula, and get back "what % chance
-- should you lock this" using the player's specific tier and games
-- remaining that week. Accuracy here matters more than almost anything
-- else in the schema for that use case.

DROP TABLE IF EXISTS hold_value_curve_params_by_tier CASCADE;

CREATE TABLE hold_value_curve_params_by_tier (
    tier      TEXT PRIMARY KEY,
    a         NUMERIC NOT NULL,
    b         NUMERIC NOT NULL,
    fitted_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION hold_win_probability_by_tier(games_remaining BIGINT, p_tier TEXT)
RETURNS NUMERIC AS $$
DECLARE
    a_val NUMERIC;
    b_val NUMERIC;
BEGIN
    IF games_remaining <= 0 THEN
        RETURN 0;  -- no future games this week -> zero chance of a later win
    END IF;

    SELECT a, b INTO a_val, b_val
    FROM hold_value_curve_params_by_tier
    WHERE tier = p_tier;

    IF a_val IS NULL THEN
        RAISE EXCEPTION 'No fitted curve for tier=%. Run scripts/fit_hold_value_curve_by_tier.py first.', p_tier;
    END IF;

    RETURN a_val * (1 - POWER(1 - b_val, games_remaining));
END;
$$ LANGUAGE plpgsql STABLE;

DROP VIEW IF EXISTS game_fantasy_scores_weekly_lock_signal;

CREATE VIEW game_fantasy_scores_weekly_lock_signal AS
SELECT
    gfsw.*,
    pt.tier,
    ROUND(1 - hold_win_probability_by_tier(gfsw.games_remaining_in_week, pt.tier), 4) AS percentage_to_lock
FROM game_fantasy_scores_weekly_effective gfsw
JOIN player_tiers pt
    ON pt.player_id = gfsw.player_id AND pt.season_id = gfsw.season_id;
-- NOTE: INNER JOIN, not LEFT -- players outside the ownable pool (not in
-- player_tiers) get no row here, matching ownable_player_pool's scope.

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM game_fantasy_scores_weekly_lock_signal;

SELECT COUNT(*) AS violations
FROM game_fantasy_scores_weekly_lock_signal
WHERE is_last_game_of_week AND percentage_to_lock != 1.0000;

-- Sanity check the refit curve against ACTUAL hold-win rates for the
-- correct (conditional) population -- should closely match
-- hold_value_by_tier_and_grw.sql's actual_hold_wins_pct column now,
-- not the old under-predicting numbers
SELECT
    tier,
    games_remaining_in_week,
    COUNT(*) AS decision_points,
    ROUND(100 * (1 - AVG(percentage_to_lock)), 1) AS implied_hold_wins_pct_from_curve
FROM game_fantasy_scores_weekly_lock_signal
WHERE games_remaining_in_week BETWEEN 1 AND 4
GROUP BY tier, games_remaining_in_week
ORDER BY tier, games_remaining_in_week;

-- Spot check Jokić's week 5, 2024-25
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week, gfsw.tier,
       gfsw.fantasy_score, gfsw.percentage_to_lock
FROM game_fantasy_scores_weekly_lock_signal gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
