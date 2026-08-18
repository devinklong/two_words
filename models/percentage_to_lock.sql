-- Fits a saturating hold-value curve, hold_wins_pct(k) = a*(1-(1-b)^k), per
-- player tier -- "if this game doesn't clear the lock bar, what's the
-- chance a later game this week beats it, given k games remain?" Feeds a
-- planned real-time tool: enter a completed game, get back a % chance you
-- should lock it. SETUP ORDER: run this file (creates an empty params
-- table), then scripts/fit_hold_value_curve_by_tier.py to populate it --
-- until that script runs, this file's own tail verification queries below
-- will error with "No fitted curve," which is expected, not a bug.

DROP TABLE IF EXISTS hold_value_curve_params_by_tier CASCADE;

CREATE TABLE hold_value_curve_params_by_tier (
    tier      TEXT PRIMARY KEY,
    a         NUMERIC NOT NULL,
    b         NUMERIC NOT NULL,
    fitted_at TIMESTAMP NOT NULL DEFAULT now()
);

-- CREATE OR REPLACE can't change a function's parameter types (would
-- overload instead), so the old BIGINT version is dropped explicitly.
-- CASCADE: game_fantasy_scores_weekly_percentage_to_lock (below) and
-- game_lock_signal.sql both transitively depend on this function -- without
-- CASCADE the drop fails outright whenever either exists, which then makes
-- every statement below silently run against the STALE pre-edit objects.
-- Always rerun game_lock_signal.sql immediately after this file.
DROP FUNCTION IF EXISTS hold_win_probability_by_tier(BIGINT, TEXT) CASCADE;

CREATE OR REPLACE FUNCTION hold_win_probability_by_tier(games_remaining NUMERIC, p_tier TEXT)
RETURNS NUMERIC AS $$
DECLARE
    a_val NUMERIC;
    b_val NUMERIC;
BEGIN
    IF games_remaining <= 0 THEN
        RETURN 0;
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

-- CASCADE here for the same reason as above -- game_lock_signal depends on this view.
DROP VIEW IF EXISTS game_fantasy_scores_weekly_percentage_to_lock CASCADE;

CREATE VIEW game_fantasy_scores_weekly_percentage_to_lock AS
SELECT
    gfsw.*,
    pt.tier,
    -- effective_games_remaining_in_week (B2B-discounted, fractional), NOT
    -- the raw count -- a future back-to-back correctly counts as slightly
    -- less than a full chance. games_remaining_in_week itself is still
    -- exposed unchanged via gfsw.* for game_lock_signal's PASS boundary,
    -- which deliberately uses the RAW count (a game doesn't disappear).
    ROUND(1 - hold_win_probability_by_tier(gfsw.effective_games_remaining_in_week, pt.tier), 4) AS percentage_to_lock
FROM game_fantasy_scores_weekly_effective gfsw
JOIN player_tiers pt
    ON pt.player_id = gfsw.player_id AND pt.season_id = gfsw.season_id;

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM game_fantasy_scores_weekly_percentage_to_lock;

-- Last game of the week must always show 100% (no future chance left)
SELECT COUNT(*) AS violations
FROM game_fantasy_scores_weekly_percentage_to_lock
WHERE is_last_game_of_week AND percentage_to_lock != 1.0000;

-- Should closely match the real hold-win rates (see fit script's own printed table)
SELECT
    tier, games_remaining_in_week,
    COUNT(*) AS decision_points,
    ROUND(100 * (1 - AVG(percentage_to_lock)), 1) AS implied_hold_wins_pct_from_curve
FROM game_fantasy_scores_weekly_percentage_to_lock
WHERE games_remaining_in_week BETWEEN 1 AND 4
GROUP BY tier, games_remaining_in_week
ORDER BY tier, games_remaining_in_week;

-- Spot check Jokić's week 5, 2024-25
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week, gfsw.tier,
       gfsw.fantasy_score, gfsw.percentage_to_lock
FROM game_fantasy_scores_weekly_percentage_to_lock gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
