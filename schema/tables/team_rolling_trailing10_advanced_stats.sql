Í-- Trailing 10-game rolling pace/ORtg/DRtg/NetRtg per team, as of BEFORE
-- each game -- same construction as team_rolling_season_to_date_stats,
-- just a bounded window instead of unbounded. See that file's header
-- for the full rationale (sum-then-divide, not average-of-per-game-
-- rates; excludes the game itself to avoid leakage).
--
-- Why a separate view instead of a parameter: Postgres views don't take
-- arguments, and a SQL function returning a table would hide the window
-- size from a plain SELECT/psql inspection -- keeping it a named view
-- makes "this is the 10-game window" visible in the schema itself, same
-- as every other view in this project. If a different window size
-- becomes useful later (5-game, 15-game), copy this file and change the
-- ROWS BETWEEN bound and the view name -- do not turn this into a
-- parameterized function without discussing it first, since the models/
-- layer would need to know which window it's calling.
--
-- games_included will be less than 10 for each team's first 10 games of
-- a season (it's however many prior games exist, capped at 10) --
-- these are naturally lower-confidence than a full window.

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

-- Sanity check: games_included should climb 0,1,2,...,9 for a team's
-- first 10 games of a season, then hold steady at 10 for every game
-- after that (never exceed 10 -- a value above 10 means the window
-- bound is wrong).
SELECT team_id, season_id, game_date, games_included
FROM team_rolling_trailing10_advanced_stats
ORDER BY team_id, season_id, game_date
LIMIT 20;
-- EXPECT: games_included values 0 through 9 for the first 10 rows of
-- any team+season, then capped at 10 for all rows after

SELECT MAX(games_included) FROM team_rolling_trailing10_advanced_stats;
-- EXPECT: 10 (never higher)

-- Spot check: same Denver game as the season-to-date view, for a direct
-- three-way eyeball (single-game vs season-to-date vs trailing-10).
SELECT tgas.game_date, tgas.pace AS single_game_pace,
       t10.games_included, t10.pace AS trailing10_pace,
       t10.off_rating AS trailing10_off_rating
FROM team_game_advanced_stats tgas
JOIN team_rolling_trailing10_advanced_stats t10
    ON t10.game_id = tgas.game_id AND t10.team_id = tgas.team_id
WHERE tgas.game_id = '0022400909' AND tgas.team_id = 1610612743;
