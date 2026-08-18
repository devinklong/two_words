-- Diagnostic: injury_penalty_targeted_check.sql's flip counts didn't
-- reconcile (80 total flips vs 71 resolved as correct/incorrect on the
-- full pool; 40 vs 37 on high-usage) -- a handful of flipped rows have
-- best_remaining_score IS NULL despite the WHERE clause already
-- requiring games_remaining_in_week >= 1. That should be structurally
-- impossible if games_remaining_in_week and the window function agree
-- on how many future games exist in the week -- this pulls those exact
-- rows to see where the disagreement is.
--
-- CENTRALIZED 8/15/26 (docs/patch_list.md #1): bar_no_penalty now calls
-- the shared lock_bar() function instead of hand-writing GREATEST(35, ...).
-- DEPLOY ORDER: lock_bar_function.sql must exist before running this.

WITH base AS (
    SELECT
        gfswls.player_id, gfswls.season_id, gfswls.week_number, gfswls.game_date,
        gfswls.fantasy_score, gfswls.games_remaining_in_week,
        pss.avg_fantasy_score AS player_avg,
        pss.stddev_fantasy_score AS player_std,
        COALESCE(pirf.is_return_game, FALSE) AS is_return_game
    FROM game_fantasy_scores_weekly_percentage_to_lock gfswls
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
        lock_bar(player_avg, player_std) AS bar_no_penalty,
        lock_bar(player_avg, player_std) + 1.5 AS bar_with_penalty,
        MAX(fantasy_score) OVER (
            PARTITION BY player_id, season_id, week_number
            ORDER BY game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score,
        -- how many rows actually exist for this player-week in `base`,
        -- and how many of them are LATER than this row's game_date --
        -- if games_remaining_in_week disagrees with this count, that's
        -- the mismatch
        COUNT(*) OVER (PARTITION BY player_id, season_id, week_number) AS rows_in_this_player_week,
        COUNT(*) OVER (
            PARTITION BY player_id, season_id, week_number
            ORDER BY game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS later_rows_actually_present
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
    player_id, season_id, week_number, game_date,
    games_remaining_in_week, later_rows_actually_present, rows_in_this_player_week,
    fantasy_score, best_remaining_score, still_locks_with_penalty
FROM return_game_locks
WHERE NOT still_locks_with_penalty
  AND best_remaining_score IS NULL
ORDER BY player_id, season_id, week_number;
