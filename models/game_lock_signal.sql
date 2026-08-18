-- Deployed LOCK/HOLD/PASS decision. LOCK requires clearing the GREATER of
-- an absolute floor (35) and the player's own mean + 0.5*stddev -- a flat
-- bar alone let high-average stars auto-LOCK on ordinary games, so the bar
-- is self-relative per player instead. Both constants were grid-searched
-- (scripts/grid_search_lock_decision.py) against a held-out validate split,
-- not guessed.
--
-- CENTRALIZED 8/15/26 (docs/patch_list.md #1): the formula itself now
-- lives in lock_bar() (models/lock_bar_function.sql), not hand-written
-- here. This view is the reason that function's defaults (floor=35,
-- ceiling_multiplier=0.5) exist -- calling lock_bar(avg, stddev) with no
-- extra args reproduces exactly what used to be hardcoded inline.
-- DEPLOY ORDER: lock_bar_function.sql, then this file.
--
-- An injury-return lock_bar penalty was built and tested here (8/10/26)
-- and REJECTED after a targeted backtest showed it was wrong more often
-- than right on the exact decisions it changed (see
-- tests/injuries/injury_penalty_targeted_check.sql for the full writeup).
-- is_return_game is kept as an exposed data column only -- same treatment
-- percentage_to_lock gets -- in case a differently-scoped correction is
-- worth trying later.

DROP VIEW IF EXISTS game_lock_signal CASCADE;

CREATE VIEW game_lock_signal AS
SELECT
    gfswls.*,
    pss.avg_fantasy_score AS player_avg_fantasy_score,
    pss.stddev_fantasy_score AS player_stddev_fantasy_score,
    COALESCE(pirf.is_return_game, FALSE) AS is_return_game,
    lock_bar(pss.avg_fantasy_score, pss.stddev_fantasy_score) AS lock_bar,
    CASE
        WHEN gfswls.fantasy_score >= lock_bar(pss.avg_fantasy_score, pss.stddev_fantasy_score)
            THEN 'LOCK'
        WHEN gfswls.games_remaining_in_week = 0 THEN 'PASS'
        ELSE 'HOLD'
    END AS lock_signal
FROM game_fantasy_scores_weekly_percentage_to_lock gfswls
JOIN ownable_player_pool opp
    ON opp.player_id = gfswls.player_id AND opp.season_id = gfswls.season_id
JOIN player_season_fantasy_stats pss
    ON pss.player_id = gfswls.player_id AND pss.season_id = gfswls.season_id
LEFT JOIN player_injury_return_flags pirf
    ON pirf.player_id = gfswls.player_id
    AND pirf.team_id = gfswls.team_id
    AND pirf.game_date = gfswls.game_date;



-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM game_lock_signal;

-- No LOCK should ever fall below the player's own dynamic lock_bar
SELECT COUNT(*) AS violations
FROM game_lock_signal
WHERE lock_signal = 'LOCK' AND fantasy_score < lock_bar;

-- Expect HOLD 52.7% / LOCK 24.2% / PASS 23.0% -- SAME as before
-- centralizing (formula unchanged, just relocated) -- if this drifts
-- from those numbers, the centralization introduced a real bug, not
-- just a refactor.
SELECT lock_signal, COUNT(*) AS game_count,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM game_lock_signal
GROUP BY lock_signal
ORDER BY game_count DESC;

-- Spot check Jokić's week 5, 2024-25 -- lock_bar should sit at ~79.5,
-- UNCHANGED from before centralizing
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.fantasy_score, gfsw.is_return_game, gfsw.lock_bar, gfsw.lock_signal
FROM game_lock_signal gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
