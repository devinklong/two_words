-- Targeted check: does the injury-return penalty actually improve the
-- specific decisions it touches, rather than diluting into noise across
-- the full weekly-outcome backtest (which is what
-- grid_search_injury_penalty.py measured -- and where the effect was
-- nearly invisible, ~0.02 pts of edge across the ENTIRE ~50k-game
-- population, because return games are only 1,653-7,669 of those games).
--
-- LOGIC: restrict to is_return_game = TRUE rows that are pool-eligible
-- and would have LOCKed under the pre-penalty rule (fantasy_score clears
-- GREATEST(35, mean + 0.5*stddev), the bar with NO injury penalty). Of
-- those, find which ones the +1.5 penalty actually FLIPS to HOLD/PASS.
-- For each flipped decision, check hindsight: did a LATER game that same
-- week score higher (best_remaining_score)? If yes, holding instead of
-- locking was the right call -- the penalty helped. If no, the player's
-- return game actually was their best score that week, and the penalty
-- wrongly talked the policy out of locking it -- the penalty hurt.
--
-- This isolates the penalty's effect on the games it can possibly change
-- (only return-game LOCK decisions near the bar), instead of averaging
-- it into ~50k mostly-unaffected games where its contribution is
-- structurally near-zero regardless of whether it's a good rule.

WITH base AS (
    SELECT
        gfswls.player_id, gfswls.season_id, gfswls.week_number, gfswls.game_date,
        gfswls.fantasy_score, gfswls.games_remaining_in_week,
        pss.avg_fantasy_score AS player_avg,
        pss.stddev_fantasy_score AS player_std,
        COALESCE(pirf.is_return_game, FALSE) AS is_return_game
    FROM game_fantasy_scores_weekly_lock_signal gfswls
    JOIN ownable_player_pool opp
        ON opp.player_id = gfswls.player_id AND opp.season_id = gfswls.season_id
    JOIN player_season_fantasy_stats pss
        ON pss.player_id = gfswls.player_id AND pss.season_id = gfswls.season_id
    LEFT JOIN player_injury_return_flags pirf
        ON pirf.player_id = gfswls.player_id
        AND pirf.team_id = gfswls.team_id
        AND pirf.game_date = gfswls.game_date
),
scored AS (
    SELECT
        *,
        GREATEST(35, player_avg + 0.5 * player_std) AS bar_no_penalty,
        GREATEST(35, player_avg + 0.5 * player_std) + 1.5 AS bar_with_penalty,
        MAX(fantasy_score) OVER (
            PARTITION BY player_id, season_id, week_number
            ORDER BY game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM base
),
return_game_locks AS (
    -- only return games that WOULD have locked under the old (no
    -- penalty) rule -- these are the only rows the penalty can possibly
    -- change the outcome for
    SELECT
        *,
        (fantasy_score >= bar_with_penalty) AS still_locks_with_penalty
    FROM scored
    WHERE is_return_game
      AND fantasy_score >= bar_no_penalty
      AND games_remaining_in_week >= 1  -- flip only matters if there's a future game to compare against; grw=0 return games always PASS/LOCK the same either way
)
SELECT
    COUNT(*) AS return_game_locks_pre_penalty,
    SUM((NOT still_locks_with_penalty)::INT) AS flipped_to_hold_by_penalty,
    SUM((still_locks_with_penalty)::INT) AS still_locked_despite_penalty,
    -- of the FLIPPED ones: how often was holding actually the right call?
    SUM((NOT still_locks_with_penalty AND best_remaining_score > fantasy_score)::INT) AS flips_where_hold_was_correct,
    SUM((NOT still_locks_with_penalty AND best_remaining_score <= fantasy_score)::INT) AS flips_where_lock_was_actually_correct,
    ROUND(
        100.0 * SUM((NOT still_locks_with_penalty AND best_remaining_score > fantasy_score)::INT)
        / NULLIF(SUM((NOT still_locks_with_penalty)::INT), 0),
        1
    ) AS pct_of_flips_that_were_correct
FROM return_game_locks;

-- Same breakdown, restricted to the high-usage population, matching how
-- the original diagnostic and grid search treated it. Own full WITH
-- chain -- CTEs don't carry across the semicolon boundary (same lesson
-- as injury_return_analysis.sql's earlier "relation capped does not
-- exist" bug -- not repeating that mistake here).
WITH base AS (
    SELECT
        gfswls.player_id, gfswls.season_id, gfswls.week_number, gfswls.game_date,
        gfswls.fantasy_score, gfswls.games_remaining_in_week,
        pss.avg_fantasy_score AS player_avg,
        pss.stddev_fantasy_score AS player_std,
        COALESCE(pirf.is_return_game, FALSE) AS is_return_game
    FROM game_fantasy_scores_weekly_lock_signal gfswls
    JOIN ownable_player_pool opp
        ON opp.player_id = gfswls.player_id AND opp.season_id = gfswls.season_id
    JOIN player_season_fantasy_stats pss
        ON pss.player_id = gfswls.player_id AND pss.season_id = gfswls.season_id
    LEFT JOIN player_injury_return_flags pirf
        ON pirf.player_id = gfswls.player_id
        AND pirf.team_id = gfswls.team_id
        AND pirf.game_date = gfswls.game_date
),
scored AS (
    SELECT
        *,
        GREATEST(35, player_avg + 0.5 * player_std) AS bar_no_penalty,
        GREATEST(35, player_avg + 0.5 * player_std) + 1.5 AS bar_with_penalty,
        MAX(fantasy_score) OVER (
            PARTITION BY player_id, season_id, week_number
            ORDER BY game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM base
),
return_game_locks AS (
    SELECT
        *,
        (fantasy_score >= bar_with_penalty) AS still_locks_with_penalty
    FROM scored
    WHERE is_return_game
      AND fantasy_score >= bar_no_penalty
      AND games_remaining_in_week >= 1
)
SELECT
    COUNT(*) AS return_game_locks_pre_penalty,
    SUM((NOT still_locks_with_penalty)::INT) AS flipped_to_hold_by_penalty,
    SUM((still_locks_with_penalty)::INT) AS still_locked_despite_penalty,
    SUM((NOT still_locks_with_penalty AND best_remaining_score > fantasy_score)::INT) AS flips_where_hold_was_correct,
    SUM((NOT still_locks_with_penalty AND best_remaining_score <= fantasy_score)::INT) AS flips_where_lock_was_actually_correct,
    ROUND(
        100.0 * SUM((NOT still_locks_with_penalty AND best_remaining_score > fantasy_score)::INT)
        / NULLIF(SUM((NOT still_locks_with_penalty)::INT), 0),
        1
    ) AS pct_of_flips_that_were_correct
FROM return_game_locks
WHERE player_avg >= 30;
