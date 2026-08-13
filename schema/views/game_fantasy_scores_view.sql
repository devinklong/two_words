-- Computes fantasy_score per game per player, using the league's exact
-- Sleeper scoring settings — pulled from sleeper_scoring_constants
-- (sourced from sleeper_leagues.scoring_settings) instead of hardcoded
-- numbers, so this view stays correct if the league's scoring settings
-- ever change. Formula verified against a known real result: Nikola
-- Jokić vs Phoenix, 3/7/25 (31 pts, 21 reb, 22 ast, 3 stl, 0 blk, 4 tov,
-- 13/22 fg, 2/3 ft, 3 fg3m) computes to exactly 113.1, matching the
-- confirmed actual Sleeper score of 113.10.
--
-- Pre-2024 seasons (game_logs goes back to 2021-22, Sleeper league data
-- only exists 2024+): LEFT JOIN + COALESCE falls back to the original
-- hardcoded constants for any season_id with no matching
-- sleeper_scoring_constants row, so backtest calibration on the full
-- 5-season set keeps computing exactly as before. 2024+ seasons pull
-- live from Sleeper.
--
-- Double-double / triple-double logic: a "double-double" is 2+ of
-- {pts, reb, ast, stl, blk} >= 10; a "triple-double" is 3+ of the same
-- categories >= 10. CONFIRMED (via the Jokić test case) that these
-- bonuses STACK — a triple-double game gets both the double-double
-- bonus AND the triple-double bonus, not one replacing the other.
--
-- The 40/50-point and 15-assist/20-rebound bonuses are intentionally
-- LEFT HARDCODED (+2/+2/+1/+1), not pulled from
-- sleeper_scoring_constants, even though Sleeper does store real keys
-- for them (bonus_pt_40p, bonus_pt_50p, bonus_ast_15p, bonus_reb_20p) —
-- decision made 8/13/26, this project is league-specific and these
-- aren't expected to change.
--
-- Known limitation, accepted: technical/flagrant foul penalties are NOT
-- included, since game_logs has no columns distinguishing them from
-- ordinary personal fouls. Games with a technical/flagrant will score
-- slightly higher here than the real Sleeper result.

DROP VIEW IF EXISTS game_fantasy_scores CASCADE;

CREATE VIEW game_fantasy_scores AS
WITH stat_lines AS (
    SELECT
        gl.game_id,
        gl.player_id,
        gl.team_id,
        gl.opponent_team_id,
        gl.season_id,
        gl.game_date,
        gl.is_home,
        gl.wl,
        gl.minutes,
        gl.pts,
        (gl.oreb + gl.dreb) AS reb,
        gl.oreb,
        gl.dreb,
        gl.ast,
        gl.stl,
        gl.blk,
        gl.tov,
        gl.fgm,
        gl.fga,
        gl.ftm,
        gl.fta,
        gl.fg3m,
        gl.fg3a,
        gl.plus_minus,
        (
            (gl.pts >= 10)::INT +
            ((gl.oreb + gl.dreb) >= 10)::INT +
            (gl.ast >= 10)::INT +
            (gl.stl >= 10)::INT +
            (gl.blk >= 10)::INT
        ) AS double_digit_categories,
        COALESCE(ssc.pts_mult, 0.5)    AS pts_mult,
        COALESCE(ssc.reb_mult, 1.5)    AS reb_mult,
        COALESCE(ssc.oreb_mult, 0.5)   AS oreb_mult,
        COALESCE(ssc.ast_mult, 2)      AS ast_mult,
        COALESCE(ssc.stl_mult, 3)      AS stl_mult,
        COALESCE(ssc.blk_mult, 3)      AS blk_mult,
        COALESCE(ssc.tov_mult, -1)     AS tov_mult,
        COALESCE(ssc.fgm_mult, 1)      AS fgm_mult,
        COALESCE(ssc.fga_mult, -0.45)  AS fga_mult,
        COALESCE(ssc.ftm_mult, 1)      AS ftm_mult,
        COALESCE(ssc.fta_mult, -0.5)   AS fta_mult,
        COALESCE(ssc.fg3m_mult, 0.5)   AS fg3m_mult,
        COALESCE(ssc.dd_bonus, 3)      AS dd_bonus,
        COALESCE(ssc.td_bonus, 5)      AS td_bonus
    FROM game_logs gl
    LEFT JOIN sleeper_scoring_constants ssc
        ON ssc.season_id = gl.season_id
)
SELECT
    game_id,
    player_id,
    team_id,
    opponent_team_id,
    season_id,
    game_date,
    is_home,
    wl,
    minutes,
    pts,
    reb,
    oreb,
    dreb,
    ast,
    stl,
    blk,
    tov,
    fgm,
    fga,
    ftm,
    fta,
    fg3m,
    fg3a,
    plus_minus,
    double_digit_categories,
    (double_digit_categories >= 2) AS is_double_double,
    (double_digit_categories >= 3) AS is_triple_double,
    ROUND(
        pts_mult  * pts
      + reb_mult  * reb
      + oreb_mult * oreb
      + ast_mult  * ast
      + stl_mult  * stl
      + blk_mult  * blk
      + tov_mult  * tov
      + fgm_mult  * fgm
      + fga_mult  * fga
      + ftm_mult  * ftm
      + fta_mult  * fta
      + fg3m_mult * fg3m
      + CASE WHEN double_digit_categories >= 2 THEN dd_bonus ELSE 0 END  -- double-double bonus
      + CASE WHEN double_digit_categories >= 3 THEN td_bonus ELSE 0 END  -- triple-double bonus (stacks)
      + CASE WHEN pts >= 40 THEN 2 ELSE 0 END                            -- 40+ points bonus (hardcoded, see header)
      + CASE WHEN pts >= 50 THEN 2 ELSE 0 END                            -- 50+ points bonus (hardcoded, stacks)
      + CASE WHEN ast >= 15 THEN 1 ELSE 0 END                            -- 15+ assists bonus (hardcoded)
      + CASE WHEN reb >= 20 THEN 1 ELSE 0 END                            -- 20+ rebounds bonus (hardcoded)
    , 2) AS fantasy_score
FROM stat_lines;

-- =========================
-- Verification
-- =========================

-- Row count should match game_logs exactly — no filtering, just added
-- columns. The LEFT JOIN fallback means this holds for all 5 seasons,
-- not just 2024+.
SELECT COUNT(*) FROM game_fantasy_scores;
SELECT COUNT(*) FROM game_logs;

-- The ground-truth test case — should still return exactly 113.10 now
-- that it's running through sleeper_scoring_constants instead of
-- hardcoded numbers
SELECT p.full_name, gfs.game_date, gfs.pts, gfs.reb, gfs.ast, gfs.stl, gfs.blk,
       gfs.is_double_double, gfs.is_triple_double, gfs.fantasy_score
FROM game_fantasy_scores gfs
JOIN players p ON p.player_id = gfs.player_id
WHERE p.full_name ILIKE '%joki%' AND gfs.game_date = '2025-03-07';

-- Sanity check the overall distribution — min/max/avg fantasy score
-- across everyone, useful gut-check before trusting this for the
-- benchmark layer
SELECT
    MIN(fantasy_score) AS min_score,
    MAX(fantasy_score) AS max_score,
    ROUND(AVG(fantasy_score), 2) AS avg_score
FROM game_fantasy_scores;

-- Highest single-game scores in the dataset — worth eyeballing for
-- plausibility (should be recognizable statlines, not obviously broken)
SELECT p.full_name, gfs.game_date, gfs.pts, gfs.reb, gfs.ast, gfs.fantasy_score
FROM game_fantasy_scores gfs
JOIN players p ON p.player_id = gfs.player_id
ORDER BY gfs.fantasy_score DESC
LIMIT 10;
