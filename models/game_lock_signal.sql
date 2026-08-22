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
-- SIMPLIFIED 8/22/26: dropped the separate join to
-- player_season_fantasy_stats entirely -- ownable_player_pool already
-- exposes avg_fantasy_score/stddev_fantasy_score directly (needed for
-- its own eligibility_ceiling calculation), so this view never needed
-- a second source for the same numbers. One join instead of two, and
-- ownable_player_pool.sql is now the only file that needs to know
-- about the season-bootstrap logic (see its header for the full
-- rationale -- season-bound stats once a player has 20+ games this
-- season, rolling last-20-games window otherwise). Because most
-- players are on the 'season' source for most of the season, the
-- LOCK/HOLD/PASS distribution should closely track the historical
-- 52.7/24.2/23.0 baseline once the season is underway -- expect
-- visible drift only early in a new season or for individual players
-- on 'rolling' due to a recent trade/injury.
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
    opp.avg_fantasy_score AS player_avg_fantasy_score,
    opp.stddev_fantasy_score AS player_stddev_fantasy_score,
    opp.stats_source AS player_stats_source,
    COALESCE(pirf.is_return_game, FALSE) AS is_return_game,
    lock_bar(opp.avg_fantasy_score, opp.stddev_fantasy_score) AS lock_bar,
    CASE
        WHEN gfswls.fantasy_score >= lock_bar(opp.avg_fantasy_score, opp.stddev_fantasy_score)
            THEN 'LOCK'
        WHEN gfswls.games_remaining_in_week = 0 THEN 'PASS'
        ELSE 'HOLD'
    END AS lock_signal
FROM game_fantasy_scores_weekly_percentage_to_lock gfswls
JOIN ownable_player_pool opp
    ON opp.player_id = gfswls.player_id AND opp.season_id = gfswls.season_id
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

-- Distribution -- should closely track 52.7/24.2/23.0 once most
-- players are on the 'season' stats source (see header). Break out by
-- source to see whether drift is coming from the still-small
-- 'rolling' population specifically, rather than a real regression.
SELECT lock_signal, COUNT(*) AS game_count,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM game_lock_signal
GROUP BY lock_signal
ORDER BY game_count DESC;

SELECT player_stats_source, lock_signal, COUNT(*) AS game_count
FROM game_lock_signal
GROUP BY player_stats_source, lock_signal
ORDER BY player_stats_source, lock_signal;
