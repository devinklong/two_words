-- Phase 2, first pass: simulates actual banked weekly scores under the
-- CURRENT calibrated policy (k=1.25, threshold=35, bucketed hold-value
-- curve) vs. a perfect-hindsight oracle vs. naive baseline B (flat
-- median threshold, 30.3, no hold-value modeling at all). This is the
-- harness the eventual (k, threshold) grid search will loop over --
-- this file runs it ONCE, against the already-calibrated v1.0 config,
-- to confirm the core simulation logic works and see where today's
-- config already stands before searching for something better.
--
-- SIMULATION RULE per player-week: walk games in date order. Bank the
-- score at the FIRST game marked LOCK. If no game that week is ever
-- LOCK, you're at the last game (games_remaining_in_week = 0, PASS by
-- definition) -- bank GREATEST(that final score, 30), since a rational
-- manager takes whichever is better between "play them anyway" and "use
-- the flat replacement-level assumption" (see methodology_notes.md for
-- why 30 is a fixed ASSUMPTION, not a derived value).
--
-- ORACLE per player-week: MAX(fantasy_score) across all their games that
-- week -- the ceiling no real policy can beat.
--
-- NAIVE BASELINE B per player-week: same walk/bank logic, but using a
-- flat 30.3 cutoff instead of the calibrated threshold + hold-value
-- curve -- tests whether the sophisticated policy actually beats the
-- simplest possible rule.

WITH first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS locked_score
    FROM game_lock_signal
    WHERE lock_signal = 'LOCK'
    ORDER BY player_id, season_id, week_number, game_date ASC
),
last_game AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS final_score
    FROM game_lock_signal
    WHERE games_remaining_in_week = 0
    ORDER BY player_id, season_id, week_number, game_date DESC
),
oracle AS (
    SELECT player_id, season_id, week_number, GREATEST(MAX(fantasy_score), 30) AS oracle_score
    FROM game_lock_signal
    GROUP BY player_id, season_id, week_number
),
-- naive baseline B: same first-lock/last-game logic, computed independently
-- with a flat 30.3 cutoff instead of the real policy
naive_first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS naive_locked_score
    FROM game_lock_signal
    WHERE fantasy_score >= 30.3
    ORDER BY player_id, season_id, week_number, game_date ASC
),
player_weeks AS (
    SELECT DISTINCT player_id, season_id, week_number FROM game_lock_signal
),
banked AS (
    SELECT
        pw.player_id, pw.season_id, pw.week_number,
        COALESCE(fl.locked_score, GREATEST(lg.final_score, 30)) AS policy_banked_score,
        COALESCE(nfl.naive_locked_score, GREATEST(lg.final_score, 30)) AS naive_banked_score,
        o.oracle_score
    FROM player_weeks pw
    JOIN oracle o USING (player_id, season_id, week_number)
    JOIN last_game lg USING (player_id, season_id, week_number)
    LEFT JOIN first_lock fl USING (player_id, season_id, week_number)
    LEFT JOIN naive_first_lock nfl USING (player_id, season_id, week_number)
)
SELECT
    COUNT(*) AS player_weeks_simulated,
    ROUND(AVG(policy_banked_score), 2) AS avg_policy_banked,
    ROUND(AVG(naive_banked_score), 2) AS avg_naive_banked,
    ROUND(AVG(oracle_score), 2) AS avg_oracle,
    ROUND(100.0 * AVG(policy_banked_score) / AVG(oracle_score), 1) AS policy_pct_of_oracle,
    ROUND(100.0 * AVG(naive_banked_score) / AVG(oracle_score), 1) AS naive_pct_of_oracle,
    ROUND(AVG(policy_banked_score) - AVG(naive_banked_score), 2) AS policy_edge_over_naive
FROM banked;

-- Same, split by train (2021-24) vs validate (2024-26) -- worth checking
-- the policy's edge over naive isn't wildly different between the two,
-- before treating today's (k=1.25, threshold=35) as validated
WITH first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS locked_score
    FROM game_lock_signal
    WHERE lock_signal = 'LOCK'
    ORDER BY player_id, season_id, week_number, game_date ASC
),
last_game AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS final_score
    FROM game_lock_signal
    WHERE games_remaining_in_week = 0
    ORDER BY player_id, season_id, week_number, game_date DESC
),
oracle AS (
    SELECT player_id, season_id, week_number, GREATEST(MAX(fantasy_score), 30) AS oracle_score
    FROM game_lock_signal
    GROUP BY player_id, season_id, week_number
),
naive_first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS naive_locked_score
    FROM game_lock_signal
    WHERE fantasy_score >= 30.3
    ORDER BY player_id, season_id, week_number, game_date ASC
),
player_weeks AS (
    SELECT DISTINCT player_id, season_id, week_number FROM game_lock_signal
),
banked AS (
    SELECT
        pw.player_id, pw.season_id, pw.week_number,
        CASE WHEN pw.season_id IN ('22021','22022','22023') THEN 'train' ELSE 'validate' END AS split,
        COALESCE(fl.locked_score, GREATEST(lg.final_score, 30)) AS policy_banked_score,
        COALESCE(nfl.naive_locked_score, GREATEST(lg.final_score, 30)) AS naive_banked_score,
        o.oracle_score
    FROM player_weeks pw
    JOIN oracle o USING (player_id, season_id, week_number)
    JOIN last_game lg USING (player_id, season_id, week_number)
    LEFT JOIN first_lock fl USING (player_id, season_id, week_number)
    LEFT JOIN naive_first_lock nfl USING (player_id, season_id, week_number)
)
SELECT
    split,
    COUNT(*) AS player_weeks_simulated,
    ROUND(AVG(policy_banked_score), 2) AS avg_policy_banked,
    ROUND(AVG(naive_banked_score), 2) AS avg_naive_banked,
    ROUND(AVG(oracle_score), 2) AS avg_oracle,
    ROUND(100.0 * AVG(policy_banked_score) / AVG(oracle_score), 1) AS policy_pct_of_oracle,
    ROUND(AVG(policy_banked_score) - AVG(naive_banked_score), 2) AS policy_edge_over_naive
FROM banked
GROUP BY split
ORDER BY split;
