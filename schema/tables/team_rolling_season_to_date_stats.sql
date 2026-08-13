-- Season-to-date rolling pace/ORtg/DRtg/NetRtg per team, as of BEFORE
-- each game (not including that game itself -- this is "what did the
-- team look like walking into this game", which is what a forward-
-- looking HOLD decision actually needs; including the game itself
-- would leak its own outcome into its own "prior form" number).
--
-- Built on team_game_advanced_stats, not raw team_game_stats -- that
-- view already has the possession estimates and points computed
-- per-game, so this just sums those across games and computes the
-- rate ONCE from the summed totals. This is deliberate: summing raw
-- totals then dividing is correct; averaging each game's already-
-- computed per-game rate is not (it weights a 60-possession game the
-- same as a 110-possession game). Same discipline as the per-game view.
--
-- games_included counts how many PRIOR games went into each row's
-- numbers. Early season rows will have a small games_included (or
-- games_included = 0, which yields NULL rates -- no data to divide
-- by yet). Treat rows with a low games_included as low-confidence,
-- same spirit as the per-game view's "one game is noisy" caveat.

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

-- Row count should match team_game_advanced_stats 1:1 (one rolling
-- snapshot per team per game, same as the per-game view)
SELECT
    (SELECT COUNT(*) FROM team_game_advanced_stats) AS advanced_stats_rows,
    (SELECT COUNT(*) FROM team_rolling_season_to_date_stats) AS rolling_rows;
-- EXPECT: rolling_rows == advanced_stats_rows

-- Sanity check: games_included should be 0 for each team's first game
-- of a season, and strictly increasing by 1 per subsequent game within
-- that team+season -- a gap or reset mid-season means the window/
-- partition logic broke.
SELECT team_id, season_id, game_date, games_included,
       games_included - LAG(games_included) OVER (
           PARTITION BY team_id, season_id ORDER BY game_date
       ) AS games_included_delta
FROM team_rolling_season_to_date_stats
ORDER BY team_id, season_id, game_date
LIMIT 20;
-- EXPECT: games_included_delta = 1 for every row except each
-- team+season's first (which should have games_included = 0)

-- Spot check: pull Denver's rolling numbers heading into the 3/7/25
-- game (game_id 0022400909) to eyeball against the per-game view's
-- already-validated single-game numbers for the same game.
SELECT tgas.game_date, tgas.pace AS single_game_pace,
       tgas.off_rating AS single_game_off_rating,
       rss.games_included, rss.pace AS season_to_date_pace,
       rss.off_rating AS season_to_date_off_rating
FROM team_game_advanced_stats tgas
JOIN team_rolling_season_to_date_stats rss
    ON rss.game_id = tgas.game_id AND rss.team_id = tgas.team_id
WHERE tgas.game_id = '0022400909' AND tgas.team_id = 1610612743;
-- Denver's team_id, per the earlier team_game_advanced_stats query result
