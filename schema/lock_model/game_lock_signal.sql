-- REDESIGNED (8/9/26, Option B) -- the lock bar is now self-relative and
-- ceiling-focused per player, not a single flat number shared by
-- everyone. Previous version used mean+k*stddev ONLY to decide POOL
-- MEMBERSHIP, then compared every individual game to a flat threshold
-- (35) regardless of who the player was -- which meant a 45-average star
-- could LOCK on a totally ordinary Tuesday, since almost every one of
-- their games clears a flat 35-44 bar. That defeats the actual point:
-- "no one wants to lock in the mean for a player even if they average 40
-- points a game, because they should have a spike game -- if they don't,
-- they're far less valuable than their average suggests."
--
-- NEW RULE: LOCK requires clearing the GREATER of an absolute floor
-- (still needed -- a bad player's personal spike can still be a
-- below-replacement score) AND their own mean + a ceiling multiplier *
-- their own stddev (a genuine, meaningfully-above-their-own-norm game,
-- not just "any decent game"). This applies to every player in the pool,
-- stars included -- Jokić's own lock_bar will typically sit well above
-- the flat 35 floor, since his mean is already ~70. Expect LOCK to
-- become noticeably rarer for high-average players under this rule --
-- that's the INTENDED effect, not a bug.
--
-- ABSOLUTE_FLOOR (35) and CEILING_MULTIPLIER (0.5) are first-pass
-- defaults, searched independently of pool membership (k=1.25,
-- threshold=35 in ownable_player_pool.sql, UNCHANGED and now fully
-- decoupled from this decision) via scripts/grid_search_lock_decision.py.
--
-- INJURY-RETURN PENALTY -- TRIED AND REJECTED (8/10/26): a +1.5 lock_bar
-- penalty on a player's return-from-injury game was built and tested
-- after injury_return_analysis.sql confirmed a real population-level dip
-- on return games (-1.46 pts, high-usage players, after fixing a
-- season_id join bug that had inflated an earlier estimate to -3.11).
-- BUT a targeted check (injury_penalty_targeted_check.sql) restricted to
-- exactly the decisions the penalty can change -- return games that
-- would have LOCKed under the old rule -- showed the penalty was WRONG
-- more often than right: only 32.5% of full-pool flips and 42.5% of
-- high-usage flips were cases where holding actually beat locking in
-- hindsight. Both well below the 50% coinflip bar. Root cause:
-- return-game LOCKs are a selected subset (players who beat the
-- population-average dip enough to still clear the bar) -- applying a
-- population-average correction to an already-above-average subset is a
-- regression-to-the-mean mismatch, not a real signal for THIS group.
-- REVERTED: penalty removed from lock_bar/lock_signal below.
-- is_return_game is still exposed as a data column (same treatment as
-- percentage_to_lock -- see percentage_to_lock.sql's own note that it's
-- exposed as data without driving the CASE logic) in case a differently
-- shaped correction (e.g. a smaller penalty, or scoped to a different
-- subset) is worth testing later. player_injury_return_flags.sql is
-- still required to run first for this join to resolve.

DROP VIEW IF EXISTS game_lock_signal CASCADE;

CREATE VIEW game_lock_signal AS
SELECT
    gfswls.*,
    pss.avg_fantasy_score AS player_avg_fantasy_score,
    pss.stddev_fantasy_score AS player_stddev_fantasy_score,
    COALESCE(pirf.is_return_game, FALSE) AS is_return_game,
    GREATEST(35, pss.avg_fantasy_score + 0.5 * pss.stddev_fantasy_score) AS lock_bar,
    CASE
        WHEN gfswls.fantasy_score >= GREATEST(35, pss.avg_fantasy_score + 0.5 * pss.stddev_fantasy_score)
            THEN 'LOCK'
        WHEN gfswls.games_remaining_in_week = 0 THEN 'PASS'
        ELSE 'HOLD'
    END AS lock_signal
FROM game_fantasy_scores_weekly_lock_signal gfswls
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

-- Confirms no LOCK ever falls below that player's own dynamic lock_bar
SELECT COUNT(*) AS violations
FROM game_lock_signal
WHERE lock_signal = 'LOCK' AND fantasy_score < lock_bar;

-- Overall distribution -- should match the pre-injury-penalty
-- distribution exactly now (HOLD 52.7% / LOCK 24.2% / PASS 23.0%),
-- since the penalty no longer affects lock_bar
SELECT lock_signal, COUNT(*) AS game_count,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM game_lock_signal
GROUP BY lock_signal
ORDER BY game_count DESC;

-- Spot check Jokić's week 5, 2024-25 -- his lock_bar should sit at
-- ~79.5 regardless of is_return_game now (penalty no longer applied)
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.fantasy_score, gfsw.is_return_game, gfsw.lock_bar, gfsw.lock_signal
FROM game_lock_signal gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
