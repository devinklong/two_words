-- Backtest harness: walks each player-week in date order, banks the score
-- at the first LOCK (or GREATEST(final_score, 30) if no LOCK fires), and
-- compares that to a perfect-hindsight oracle and a naive flat-30.3-cutoff
-- baseline. This is the metric grid_search_lock_decision.py optimizes and
-- the tool used to validate/reject the injury-return penalty -- rerun it
-- any time the model changes.

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
    -- GREATEST(..., 30): even the oracle can't bank below replacement level
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

-- Same, split train (2021-24) / validate (2024-26) -- the edge shouldn't differ wildly between them
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
