-- Raw team-level box score components, one row per team per game (2 rows
-- per game_id, same shape as game_logs but at the team level instead of
-- player level). Stores ONLY raw counting stats -- no percentages, no
-- pace/ORtg/DRtg/NetRtg -- those are all derivable from these columns
-- and belong in a view (schema/tables/team_advanced_stats.sql, built
-- separately once this table is populated), same pattern
-- game_fantasy_scores already uses for fantasy_score.
--
-- Source: BoxScoreTraditionalV3's team-stats frame (index 2 of
-- get_data_frames() -- confirmed 8/10/26, same frame data_cleaning_
-- boxscore.py already pulls `points` from for WL derivation). That
-- frame has no home/away indicator, so is_home/opponent_team_id still
-- come from get_scoreboard_games.py's leaderType-based derivation, same
-- as game_logs.

DROP TABLE IF EXISTS team_game_stats;

CREATE TABLE team_game_stats (
    game_id            TEXT    NOT NULL,
    team_id            BIGINT  NOT NULL,
    opponent_team_id   BIGINT  NOT NULL,
    season_id          TEXT    NOT NULL,
    game_date          DATE    NOT NULL,
    is_home            BOOLEAN NOT NULL,
    fgm                INTEGER NOT NULL,
    fga                INTEGER NOT NULL,
    fg3m               INTEGER NOT NULL,
    fg3a               INTEGER NOT NULL,
    ftm                INTEGER NOT NULL,
    fta                INTEGER NOT NULL,
    oreb               INTEGER NOT NULL,
    dreb               INTEGER NOT NULL,
    ast                INTEGER NOT NULL,
    stl                INTEGER NOT NULL,
    blk                INTEGER NOT NULL,
    tov                INTEGER NOT NULL,
    pf                 INTEGER NOT NULL,
    pts                INTEGER NOT NULL,
    plus_minus         NUMERIC,
    PRIMARY KEY (game_id, team_id)
);

CREATE INDEX idx_team_game_stats_team_season_date
ON team_game_stats (team_id, season_id, game_date);

-- =========================
-- Verification
-- =========================

-- Expect exactly 2 rows per game_id (every completed game has two teams)
SELECT COUNT(*) AS games_with_wrong_row_count
FROM (
    SELECT game_id, COUNT(*) AS n
    FROM team_game_stats
    GROUP BY game_id
    HAVING COUNT(*) != 2
) sub;
-- EXPECT: 0

SELECT COUNT(*) FROM team_game_stats;
SELECT COUNT(DISTINCT game_id) FROM team_game_stats;
