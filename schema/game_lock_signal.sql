-- The v1.0 deliverable: combines the league-relative absolute bar
-- (ownable_player_pool's eligibility, plus a flat per-game threshold) with
-- the player-relative, bucketed hold-value layer
-- (game_fantasy_scores_weekly_lock_signal.percentage_to_lock) into a
-- LOCK / HOLD / PASS decision per game, restricted to the ownable pool
-- (the ~150-210 player-seasons/season this tool is actually meant to
-- advise on).
--
-- DECISION LOGIC (redesigned 8/8/26, see note below):
--   1. fantasy_score >= LOCK_THRESHOLD (35) -> LOCK. Clears the flat,
--      player-independent absolute bar on its own merits.
--   2. Below the bar, games_remaining_in_week = 0 (no more chances this
--      week) -> PASS. This score never cleared the bar and there's no
--      more shot at a better one from THIS player this week — the
--      correct signal is to look elsewhere (waivers), not to lock in a
--      below-bar score just because it's the only option left.
--   3. Below the bar, games remaining >= 1 -> HOLD. A real future chance
--      still exists; percentage_to_lock (already on the row) is the
--      confidence figure for how strong that chance is, exposed as data
--      rather than pre-baked into more discrete categories.
--
-- REDESIGN NOTE (8/8/26): the original version had a 4th branch —
-- percentage_to_lock >= some cutoff (tried 0.55, then 0.80) -> LOCK
-- anyway, even below the absolute bar. This was WRONG, not just
-- miscalibrated: it let a genuinely bad score (e.g. 10.85, nowhere near
-- the 35 bar) get labeled LOCK purely because games_remaining_in_week=0
-- made percentage_to_lock hit its fixed 1.0 ceiling. That's backwards —
-- "no more chances to do better" is a reason to PASS on a bad score, not
-- a reason to call it good. The absolute bar has to gate LOCK
-- unconditionally; hold-value should only ever choose between HOLD and
-- PASS for scores that didn't clear it. This also removes two arbitrary,
-- never-validated cutoff constants (0.55/0.80) from the design entirely,
-- rather than continuing to hand-tune them.
--
-- LOCK_THRESHOLD (35) is still first-pass/tunable, same status as
-- k/threshold in ownable_player_pool.sql — Phase 2's train/test backtest
-- is the intended mechanism for real calibration, not this file.

DROP VIEW IF EXISTS game_lock_signal;

CREATE VIEW game_lock_signal AS
SELECT
    gfswls.*,
    CASE
        WHEN gfswls.fantasy_score >= 35 THEN 'LOCK'
        WHEN gfswls.games_remaining_in_week = 0 THEN 'PASS'
        ELSE 'HOLD'
    END AS lock_signal
FROM game_fantasy_scores_weekly_lock_signal gfswls
JOIN ownable_player_pool opp
    ON opp.player_id = gfswls.player_id AND opp.season_id = gfswls.season_id;

-- =========================
-- Verification
-- =========================

-- Overall LOCK/HOLD/PASS distribution
SELECT lock_signal, COUNT(*) AS game_count,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM game_lock_signal
GROUP BY lock_signal
ORDER BY game_count DESC;

-- Confirms the actual bug is fixed: zero rows should exist where
-- lock_signal = 'LOCK' but fantasy_score is below the 35 bar
SELECT COUNT(*) AS violations
FROM game_lock_signal
WHERE lock_signal = 'LOCK' AND fantasy_score < 35;

-- Same breakdown split by variance_bucket
SELECT variance_bucket, lock_signal, COUNT(*) AS game_count
FROM game_lock_signal
GROUP BY variance_bucket, lock_signal
ORDER BY variance_bucket, lock_signal;

-- Spot check Jokić's week 5, 2024-25
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.fantasy_score, gfsw.percentage_to_lock, gfsw.lock_signal
FROM game_lock_signal gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;

-- Rerun the SAME low-average/high-variance player spot check from before
-- (Jaime Jaquez Jr., or whoever currently matches) to confirm none of his
-- below-35 games say LOCK anymore
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.fantasy_score, gfsw.percentage_to_lock, gfsw.lock_signal
FROM game_lock_signal gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE gfsw.player_id = (
    SELECT opp.player_id
    FROM ownable_player_pool opp
    JOIN player_variance_buckets pvb
        ON pvb.player_id = opp.player_id AND pvb.season_id = opp.season_id
    WHERE opp.season_id = '22024' AND pvb.variance_bucket = 2
    ORDER BY opp.avg_fantasy_score ASC
    LIMIT 1
)
AND gfsw.season_id = '22024'
ORDER BY gfsw.game_date
LIMIT 20;
