-- Stacks single-game/season-to-date/trailing-10 into one view with a
-- `granularity` label column; convenience layer only, source views stay
-- the source of truth. `games_included` is NULL for single_game rows
-- (n=1, not missing).

DROP VIEW IF EXISTS team_stats_all_granularities;

CREATE VIEW team_stats_all_granularities AS
SELECT
    'single_game'::TEXT AS granularity,
    game_id, team_id, opponent_team_id, season_id, game_date, is_home,
    NULL::BIGINT AS games_included,
    pace, off_rating, def_rating, net_rating
FROM team_game_advanced_stats

UNION ALL

SELECT
    'season_to_date'::TEXT AS granularity,
    game_id, team_id, opponent_team_id, season_id, game_date, is_home,
    games_included,
    pace, off_rating, def_rating, net_rating
FROM team_rolling_season_to_date_stats

UNION ALL

SELECT
    'trailing_10'::TEXT AS granularity,
    game_id, team_id, opponent_team_id, season_id, game_date, is_home,
    games_included,
    pace, off_rating, def_rating, net_rating
FROM team_rolling_trailing10_advanced_stats;

-- =========================
-- Verification
-- =========================

-- Should be exactly 3x team_game_advanced_stats' row count.
SELECT
    (SELECT COUNT(*) FROM team_game_advanced_stats) AS base_rows,
    (SELECT COUNT(*) FROM team_stats_all_granularities) AS stacked_rows;
-- EXPECT: stacked_rows == base_rows * 3

SELECT granularity, COUNT(*) FROM team_stats_all_granularities GROUP BY granularity;
-- EXPECT: three rows, each count == base_rows
