-- Incremental catch-up sync for game_fantasy_scores_weekly_effective
-- (now a real table, see migrate_game_fantasy_scores_weekly_effective_
-- to_table.sql). Anti-joins against game_fantasy_scores_weekly_full (the
-- underlying plain view) to find rows that exist there but not yet in
-- the table, computes effective_games_remaining_in_week for ONLY those
-- rows, and inserts them.
--
-- DELIBERATELY NOT scoped to a specific date -- it catches whatever's
-- actually missing regardless of which script inserted the underlying
-- game_logs rows (the daily loader, backfill_missing_players.py,
-- backfill_single_player.py, all of them). This means a 2-way/bench
-- player backfilled through any path gets picked up automatically here,
-- without needing to know in advance who they are or when they were
-- added -- the anti-join finds the gap on its own.
--
-- Safe to run as often as needed -- a run with nothing new to catch up
-- on is just a fast no-op (the anti-join returns zero rows).
--
-- Run standalone:
--   psql -h 127.0.0.1 -U devinlong -d postgres -f schema/sync_game_fantasy_scores_weekly_effective.sql
-- Also chained automatically at the end of load_daily_game_logs.py.

INSERT INTO game_fantasy_scores_weekly_effective
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
FROM game_fantasy_scores_weekly_full gfsw
LEFT JOIN game_fantasy_scores_weekly_effective existing
    ON existing.player_id = gfsw.player_id AND existing.game_id = gfsw.game_id
WHERE existing.player_id IS NULL;

-- =========================
-- Verification
-- =========================

-- How many rows this run actually caught up (0 is a normal, healthy
-- result if nothing new landed in game_logs since the last sync)
SELECT COUNT(*) AS total_rows FROM game_fantasy_scores_weekly_effective;

SELECT COUNT(*) AS violations
FROM game_fantasy_scores_weekly_effective
WHERE effective_games_remaining_in_week > games_remaining_in_week;
