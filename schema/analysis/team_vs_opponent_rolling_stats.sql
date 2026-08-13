-- Pulls each team's own rolling stats and its opponent's rolling stats
-- into one row per team-game -- the scaffold step 5 needs for testing
-- own-team vs. opponent-team effects separately. Two views, one per
-- rolling window, rather than one mega-view mixing games_included
-- semantics that differ between the two windows.

DROP VIEW IF EXISTS team_vs_opponent_season_to_date;

CREATE VIEW team_vs_opponent_season_to_date AS
SELECT
    own.game_id, own.team_id, own.opponent_team_id, own.season_id,
    own.game_date, own.is_home,
    own.games_included AS own_games_included,
    own.pace AS own_pace, own.off_rating AS own_off_rating,
    own.def_rating AS own_def_rating, own.net_rating AS own_net_rating,
    opp.games_included AS opp_games_included,
    opp.pace AS opp_pace, opp.off_rating AS opp_off_rating,
    opp.def_rating AS opp_def_rating, opp.net_rating AS opp_net_rating
FROM team_rolling_season_to_date_stats own
JOIN team_rolling_season_to_date_stats opp
    ON opp.game_id = own.game_id AND opp.team_id = own.opponent_team_id;

DROP VIEW IF EXISTS team_vs_opponent_trailing10;

CREATE VIEW team_vs_opponent_trailing10 AS
SELECT
    own.game_id, own.team_id, own.opponent_team_id, own.season_id,
    own.game_date, own.is_home,
    own.games_included AS own_games_included,
    own.pace AS own_pace, own.off_rating AS own_off_rating,
    own.def_rating AS own_def_rating, own.net_rating AS own_net_rating,
    opp.games_included AS opp_games_included,
    opp.pace AS opp_pace, opp.off_rating AS opp_off_rating,
    opp.def_rating AS opp_def_rating, opp.net_rating AS opp_net_rating
FROM team_rolling_trailing10_advanced_stats own
JOIN team_rolling_trailing10_advanced_stats opp
    ON opp.game_id = own.game_id AND opp.team_id = own.opponent_team_id;

-- =========================
-- Verification
-- =========================

SELECT
    (SELECT COUNT(*) FROM team_rolling_season_to_date_stats) AS base_rows,
    (SELECT COUNT(*) FROM team_vs_opponent_season_to_date) AS joined_rows;
-- EXPECT: joined_rows == base_rows (1:1).

SELECT
    (SELECT COUNT(*) FROM team_rolling_trailing10_advanced_stats) AS base_rows,
    (SELECT COUNT(*) FROM team_vs_opponent_trailing10) AS joined_rows;
-- EXPECT: joined_rows == base_rows

-- =========================
-- 8-hypothesis scaffold (roadmap step 5); off_rating is a stand-in
-- target below -- swap for the real percentage_to_lock-relevant outcome.
-- =========================

-- Does a team's own pace predict its own output?
SELECT ROUND(CORR(own_pace, own_off_rating)::NUMERIC, 3) AS corr_own_pace_own_off_rating
FROM team_vs_opponent_trailing10;

-- Does the opponent's pace predict this team's output?
SELECT ROUND(CORR(opp_pace, own_off_rating)::NUMERIC, 3) AS corr_opp_pace_own_off_rating
FROM team_vs_opponent_trailing10;

-- Does the opponent's defense predict this team's offense?
SELECT ROUND(CORR(opp_def_rating, own_off_rating)::NUMERIC, 3) AS corr_opp_def_own_off_rating
FROM team_vs_opponent_trailing10;

-- Does the opponent's offense predict this team's defense?
SELECT ROUND(CORR(opp_off_rating, own_def_rating)::NUMERIC, 3) AS corr_opp_off_own_def_rating
FROM team_vs_opponent_trailing10;

-- Does the pace GAP (own minus opponent) relate to own output?
SELECT
    ROUND(CORR(own_pace - opp_pace, own_off_rating)::NUMERIC, 3) AS corr_pace_gap_own_off_rating
FROM team_vs_opponent_trailing10;

-- Repeat swapping in team_vs_opponent_season_to_date to check both windows.
