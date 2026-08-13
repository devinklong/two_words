-- Per-game pace/ORtg/DRtg/NetRtg, computed from team_game_stats' raw
-- counting stats -- no stored percentages, same formula-in-a-view
-- pattern game_fantasy_scores already uses for fantasy_score.
--
-- Possessions estimate (standard simplified form): FGA - OREB + TOV +
-- 0.4*FTA, per team. Pace is the AVERAGE of both teams' estimates for
-- that game (the two should be close but rarely identical -- averaging
-- is the conventional approach, not this project's own invention).
-- Ratings use that same averaged pace as the possessions denominator for
-- both ORtg and DRtg, so they're internally consistent with each other.
--
-- NOT YET VALIDATED against a real published source (same discipline
-- fantasy_score got before being trusted -- see methodology_notes.md).
-- Run the spot check at the bottom against a real site's stated pace/
-- rating for the same game before trusting this for anything beyond
-- exploration.
--
-- PER-GAME ONLY -- this does not answer "is this team's pace/rating
-- currently good," since one game is a tiny, noisy sample. The rolling/
-- season-to-date aggregation (step 3, not yet built) is what's actually
-- meant to inform the HOLD/percentage_to_lock decision -- see
-- methodology_notes.md's v2.0 roadmap for why per-game and rolling are
-- deliberately separate steps.

DROP VIEW IF EXISTS team_game_advanced_stats;

CREATE VIEW team_game_advanced_stats AS
WITH game_teams AS (
    SELECT
        tgs.game_id, tgs.team_id, tgs.opponent_team_id, tgs.season_id,
        tgs.game_date, tgs.is_home, tgs.fga, tgs.fta, tgs.oreb, tgs.tov, tgs.pts,
        opp.fga AS opp_fga, opp.fta AS opp_fta, opp.oreb AS opp_oreb,
        opp.tov AS opp_tov, opp.pts AS opp_pts
    FROM team_game_stats tgs
    JOIN team_game_stats opp
        ON opp.game_id = tgs.game_id AND opp.team_id = tgs.opponent_team_id
),
with_possessions AS (
    SELECT
        *,
        (fga - oreb + tov + 0.4 * fta) AS team_poss_est,
        (opp_fga - opp_oreb + opp_tov + 0.4 * opp_fta) AS opp_poss_est
    FROM game_teams
),
with_pace AS (
    SELECT
        *,
        (team_poss_est + opp_poss_est) / 2.0 AS pace_raw
    FROM with_possessions
),
with_ratings AS (
    SELECT
        *,
        (100.0 * pts / NULLIF(pace_raw, 0)) AS off_rating_raw,
        (100.0 * opp_pts / NULLIF(pace_raw, 0)) AS def_rating_raw
    FROM with_pace
)
SELECT
    game_id, team_id, opponent_team_id, season_id, game_date, is_home,
    pts, opp_pts,
    ROUND(team_poss_est::NUMERIC, 2) AS team_possessions_est,
    ROUND(opp_poss_est::NUMERIC, 2) AS opp_possessions_est,
    ROUND(pace_raw::NUMERIC, 2) AS pace,
    ROUND(off_rating_raw::NUMERIC, 2) AS off_rating,
    ROUND(def_rating_raw::NUMERIC, 2) AS def_rating,
    ROUND((off_rating_raw - def_rating_raw)::NUMERIC, 2) AS net_rating
FROM with_ratings;

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM team_game_advanced_stats;

-- Should be exactly 2x team_game_stats' row count (every row finds its
-- opponent via the self-join) -- a shortfall means some game has only
-- one team's row in team_game_stats (a real data gap) or a team_id
-- mismatch broke the join
SELECT
    (SELECT COUNT(*) FROM team_game_stats) AS team_game_stats_rows,
    (SELECT COUNT(*) FROM team_game_advanced_stats) AS advanced_stats_rows;
-- EXPECT: advanced_stats_rows == team_game_stats_rows (1:1, not 2x --
-- the self-join finds each row's OWN opponent, doesn't duplicate rows)

-- Spot check: Denver vs Phoenix, 3/7/25 (real final score DEN 149, PHX
-- 141, OT -- known from earlier verification in this project). Cross-
-- check the pace/off_rating/def_rating below against a real published
-- source (NBA.com's Advanced team stats page, Basketball-Reference) for
-- this specific game before trusting the formula.
SELECT tgas.*, t.full_name AS team_name
FROM team_game_advanced_stats tgas
JOIN teams t ON t.team_id = tgas.team_id
WHERE tgas.game_id = '0022400909'
ORDER BY tgas.is_home DESC;