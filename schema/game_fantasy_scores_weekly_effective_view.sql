-- Applies the confirmed B2B fatigue effect (b2b_analysis.sql) to
-- games_remaining_in_week: instead of counting every remaining game as
-- equal value, a remaining game that will be the second night of a
-- back-to-back is weighted at a discount rather than full value.
--
-- Discount factor derivation: high-usage players (season avg 30+) averaged
-- 38.44 fantasy pts on normal rest vs 37.69 on the second night of a B2B —
-- 37.69 / 38.44 = 0.9805. Using the high-usage ratio specifically (not the
-- full-population ~1.6% figure) since that's the population this tool
-- actually targets. This is a first-pass empirical estimate, not a
-- rigorously fit parameter — worth treating as provisional and revisiting
-- once the Phase 2 backtest exists to test it against real outcomes rather
-- than a single ratio.

DROP VIEW IF EXISTS game_fantasy_scores_weekly_effective;

CREATE VIEW game_fantasy_scores_weekly_effective AS
SELECT
    gfsw.*,
    (
        SELECT COALESCE(ROUND(SUM(
            CASE WHEN b2b.is_second_night_of_b2b THEN 0.9805 ELSE 1.0 END
        ), 3), 0)
        FROM team_schedule_b2b_flags b2b
        WHERE b2b.team_id = gfsw.team_id
          AND b2b.season_id = gfsw.season_id
          AND b2b.game_date > gfsw.game_date
          AND b2b.game_date BETWEEN gfsw.week_start_date AND gfsw.week_end_date
    ) AS effective_games_remaining_in_week
FROM game_fantasy_scores_weekly_full gfsw;

-- =========================
-- Verification
-- =========================

-- Row count should still match exactly — only adding a column
SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective;
SELECT COUNT(*) FROM game_fantasy_scores_weekly_full;

-- effective_games_remaining_in_week should NEVER exceed the raw count,
-- since the discount factor is always <= 1.0
SELECT COUNT(*) AS violations
FROM game_fantasy_scores_weekly_effective
WHERE effective_games_remaining_in_week > games_remaining_in_week;

-- Where they actually differ — should be exactly the rows where at least
-- one remaining game in the week is a B2B second night
SELECT COUNT(*) AS rows_with_discount_applied
FROM game_fantasy_scores_weekly_effective
WHERE effective_games_remaining_in_week < games_remaining_in_week;

-- Spot check Jokić's week 5, 2024-25 — raw vs effective side by side
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.effective_games_remaining_in_week, gfsw.is_last_game_of_week
FROM game_fantasy_scores_weekly_effective gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
