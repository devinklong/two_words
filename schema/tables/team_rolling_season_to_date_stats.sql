-- Season-to-date rolling pace/ORtg/DRtg/NetRtg per team, as of BEFORE
-- each game (excludes the game itself, avoiding leakage). Sums raw
-- totals across games then computes the rate once, rather than
-- averaging per-game rates. games_included = 0 means no prior games
-- yet (rates come back NULL); low games_included = low confidence.

DROP VIEW IF EXISTS team_rolling_season_to_date_stats;

CREATE VIEW team_rolling_season_to_date_stats AS
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
        ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
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

SELECT COUNT(*) FROM team_rolling_season_to_date_stats;

-- Row count should match team_game_advanced_stats 1:1.
SELECT
    (SELECT COUNT(*) FROM team_game_advanced_stats) AS advanced_stats_rows,
    (SELECT COUNT(*) FROM team_rolling_season_to_date_stats) AS rolling_rows;
-- EXPECT: rolling_rows == advanced_stats_rows

-- games_included should start at 0 and increase by 1 per game within a team+season.
SELECT team_id, season_id, game_date, games_included,
       games_included - LAG(games_included) OVER (
           PARTITION BY team_id, season_id ORDER BY game_date
       ) AS games_included_delta
FROM team_rolling_season_to_date_stats
ORDER BY team_id, season_id, game_date
LIMIT 20;
-- EXPECT: games_included_delta = 1 for every row except each
-- team+season's first (which should have games_included = 0)

-- Spot check: Denver's rolling numbers heading into 3/7/25 vs that game's single-game numbers.
SELECT tgas.game_date, tgas.pace AS single_game_pace,
       tgas.off_rating AS single_game_off_rating,
       rss.games_included, rss.pace AS season_to_date_pace,
       rss.off_rating AS season_to_date_off_rating
FROM team_game_advanced_stats tgas
JOIN team_rolling_season_to_date_stats rss
    ON rss.game_id = tgas.game_id AND rss.team_id = tgas.team_id
WHERE tgas.game_id = '0022400909' AND tgas.team_id = 1610612743;
-- 1610612743 is Denver's team_id.
