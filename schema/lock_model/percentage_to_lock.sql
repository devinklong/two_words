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
--
-- B2B WIRING (8/9/26): now reads effective_games_remaining_in_week
-- (B2B-discounted, fractional) instead of the raw integer count, so a
-- remaining game that's a back-to-back second night correctly counts as
-- slightly less than a full future chance. Required widening
-- hold_win_probability_by_tier's parameter from BIGINT to NUMERIC.

DROP TABLE IF EXISTS hold_value_curve_params_by_tier CASCADE;

CREATE TABLE hold_value_curve_params_by_tier (
    tier      TEXT PRIMARY KEY,
    a         NUMERIC NOT NULL,
    b         NUMERIC NOT NULL,
    fitted_at TIMESTAMP NOT NULL DEFAULT now()
);

-- CREATE OR REPLACE does NOT replace a function whose parameter types
-- changed -- it would create a second, overloaded version instead,
-- leaving the old BIGINT one as dead weight. Drop it explicitly first.
--
-- CASCADE (added 8/10/26): game_fantasy_scores_weekly_lock_signal (below,
-- same file) AND game_lock_signal (schema/lock_model/game_lock_signal.sql)
-- both depend on this function, transitively. Without CASCADE, this DROP
-- fails outright the moment either of those exists from a prior run,
-- which also means the later DROP VIEW / CREATE VIEW statements in this
-- file silently fail too (view "already exists") and everything below
-- keeps running against the STALE pre-edit function/view -- no error
-- surfaces except at the DROP statements themselves, so it's easy to miss.
-- CASCADE takes game_lock_signal down with it -- ALWAYS rerun
-- game_lock_signal.sql immediately after this file, never standalone.
DROP FUNCTION IF EXISTS hold_win_probability_by_tier(BIGINT, TEXT) CASCADE;

CREATE OR REPLACE FUNCTION hold_win_probability_by_tier(games_remaining NUMERIC, p_tier TEXT)
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

-- CASCADE (added 8/10/26): game_lock_signal depends on this view. See
-- note above the function drop -- same failure mode, same fix.
DROP VIEW IF EXISTS game_fantasy_scores_weekly_lock_signal CASCADE;

CREATE VIEW game_fantasy_scores_weekly_lock_signal AS
SELECT
    gfsw.*,
    pt.tier,
    ROUND(1 - hold_win_probability_by_tier(gfsw.effective_games_remaining_in_week, pt.tier), 4) AS percentage_to_lock
FROM game_fantasy_scores_weekly_effective gfsw
JOIN player_tiers pt
    ON pt.player_id = gfsw.player_id AND pt.season_id = gfsw.season_id;
-- NOTE: uses effective_games_remaining_in_week (B2B-discounted,
-- fractional), NOT games_remaining_in_week, so a future back-to-back
-- game correctly counts as slightly less than a full "chance" in the
-- hold-value curve. games_remaining_in_week itself is still exposed via
-- gfsw.* unchanged -- game_lock_signal.sql's PASS boundary
-- (games_remaining_in_week = 0) deliberately still uses the RAW count,
-- not this effective one, since B2B fatigue discounts a game's expected
-- VALUE, it doesn't make a real scheduled game disappear.

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
