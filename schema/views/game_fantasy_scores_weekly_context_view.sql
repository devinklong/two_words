-- Adds weekly game-count context to every row in game_fantasy_scores_weekly:
-- how many of that player's TEAM's scheduled games (per team_schedule, not
-- game_logs) fall before/after this game within the same fantasy week.
-- Uses team_schedule deliberately, not game_logs, since the count needs to
-- reflect the team's actual remaining games that week regardless of
-- whether the player personally plays in all of them (DNPs, injuries,
-- coach's decisions shouldn't shrink this count — the SLOT still has
-- games scheduled, which is what matters for hold-value).
--
-- This is the direct input for the sliding lock-threshold design: early in
-- the week with games remaining, the bar to lock should be higher (more
-- chances to beat it later); on the last remaining game, the bar should be
-- lower (no more chances this week).

DROP VIEW IF EXISTS game_fantasy_scores_weekly_context;

CREATE VIEW game_fantasy_scores_weekly_context AS
SELECT
    gfsw.*,
    (
        SELECT COUNT(*)
        FROM team_schedule ts
        WHERE ts.team_id = gfsw.team_id
          AND ts.season_id = gfsw.season_id
          AND ts.game_date < gfsw.game_date
          AND ts.game_date BETWEEN gfsw.week_start_date AND gfsw.week_end_date
    ) AS games_before_in_week,
    (
        SELECT COUNT(*)
        FROM team_schedule ts
        WHERE ts.team_id = gfsw.team_id
          AND ts.season_id = gfsw.season_id
          AND ts.game_date > gfsw.game_date
          AND ts.game_date BETWEEN gfsw.week_start_date AND gfsw.week_end_date
    ) AS games_remaining_in_week,
    (
        SELECT COUNT(*)
        FROM team_schedule ts
        WHERE ts.team_id = gfsw.team_id
          AND ts.season_id = gfsw.season_id
          AND ts.game_date BETWEEN gfsw.week_start_date AND gfsw.week_end_date
    ) AS total_team_games_this_week
FROM game_fantasy_scores_weekly gfsw;

-- Convenience column, split out separately since it depends on the ones
-- above and keeping it in a second view avoids repeating all 3 subqueries
DROP VIEW IF EXISTS game_fantasy_scores_weekly_full;

CREATE VIEW game_fantasy_scores_weekly_full AS
SELECT
    *,
    (games_remaining_in_week = 0) AS is_last_game_of_week
FROM game_fantasy_scores_weekly_context;

-- =========================
-- Verification
-- =========================

-- Row count should still match game_fantasy_scores_weekly exactly — this
-- only adds columns, no filtering
SELECT COUNT(*) FROM game_fantasy_scores_weekly_full;
SELECT COUNT(*) FROM game_fantasy_scores_weekly;

-- Internal consistency check: games_before + 1 (this game) + games_remaining
-- should always equal total_team_games_this_week

CREATE INDEX idx_team_schedule_team_season_date
ON team_schedule (team_id, season_id, game_date);
SELECT COUNT(*) AS inconsistent_rows
FROM game_fantasy_scores_weekly_full
WHERE games_before_in_week + 1 + games_remaining_in_week != total_team_games_this_week;

-- Spot check Jokić's week 5, 2024-25 — confirm games_remaining_in_week
-- counts down correctly across the week's games in order
SELECT p.full_name, gfsw.game_date, gfsw.games_before_in_week,
       gfsw.games_remaining_in_week, gfsw.total_team_games_this_week,
       gfsw.is_last_game_of_week, gfsw.fantasy_score
FROM game_fantasy_scores_weekly_full gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;

-- Distribution of total_team_games_this_week across all rows — most teams
-- play 3-4 games in a normal week, worth eyeballing this isn't wildly off
SELECT total_team_games_this_week, COUNT(*) AS row_count
FROM game_fantasy_scores_weekly_full
GROUP BY total_team_games_this_week
ORDER BY total_team_games_this_week;
