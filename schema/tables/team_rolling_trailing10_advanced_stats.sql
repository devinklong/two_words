-- Trailing 10-game rolling pace/ORtg/DRtg/NetRtg per team, as of BEFORE
-- each game -- same construction as team_rolling_season_to_date_stats,
-- just a bounded window. A separate named view rather than a
-- parameterized function, so the window size stays visible in the
-- schema; copy this file if a different window size is ever needed.
-- games_included caps at 10 and is lower for a team's first 10 games.

DROP VIEW IF EXISTS team_rolling_trailing10_advanced_stats;

CREATE VIEW team_rolling_trailing10_advanced_stats AS
WITH windowed AS (
    SELECT
        game_id, team_id, opponent_team_id, season_id, game_date, is_home,
        pts, opp_pts,
        SUM(pts) OVER w AS cum_pts,
        SUM(opp_pts) OVER w AS cum_opp_pts,
        SUM(team_possessions_est) OVER w AS cum_team_poss,
        SUM(opp_possessions_est) OVER w AS cum_opp_poss,
        COUNT(*) OVER w AS games_included
    FROM team_game_advanced_stats
    WINDOW w AS (
        PARTITION BY team_id, season_id
        ORDER BY game_date
        ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
    )
),
with_basis AS (
    SELECT
        *,
        (cum_team_poss + cum_opp_poss) / 2.0 AS poss_basis
    FROM windowed
)
SELECT
    game_id, team_id, opponent_team_id, season_id, game_date, is_home,
    games_included,
    ROUND((poss_basis / NULLIF(games_included, 0))::NUMERIC, 2) AS pace,
    ROUND((100.0 * cum_pts / NULLIF(poss_basis, 0))::NUMERIC, 2) AS off_rating,
    ROUND((100.0 * cum_opp_pts / NULLIF(poss_basis, 0))::NUMERIC, 2) AS def_rating,
    ROUND(((100.0 * cum_pts / NULLIF(poss_basis, 0))
         - (100.0 * cum_opp_pts / NULLIF(poss_basis, 0)))::NUMERIC, 2) AS net_rating
FROM with_basis;

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM team_rolling_trailing10_advanced_stats;

SELECT
    (SELECT COUNT(*) FROM team_game_advanced_stats) AS advanced_stats_rows,
    (SELECT COUNT(*) FROM team_rolling_trailing10_advanced_stats) AS rolling_rows;
-- EXPECT: rolling_rows == advanced_stats_rows

-- games_included should climb 0-9 then cap at 10 per team+season.
SELECT team_id, season_id, game_date, games_included
FROM team_rolling_trailing10_advanced_stats
ORDER BY team_id, season_id, game_date
LIMIT 20;

SELECT MAX(games_included) FROM team_rolling_trailing10_advanced_stats;
-- EXPECT: 10 (never higher)

-- Spot check: same Denver game as the season-to-date view, for a three-way eyeball.
SELECT tgas.game_date, tgas.pace AS single_game_pace,
       t10.games_included, t10.pace AS trailing10_pace,
       t10.off_rating AS trailing10_off_rating
FROM team_game_advanced_stats tgas
JOIN team_rolling_trailing10_advanced_stats t10
    ON t10.game_id = tgas.game_id AND t10.team_id = tgas.team_id
WHERE tgas.game_id = '0022400909' AND tgas.team_id = 1610612743;
