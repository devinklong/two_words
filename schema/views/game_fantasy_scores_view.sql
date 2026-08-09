-- Computes fantasy_score per game per player, using the league's exact
-- Sleeper scoring settings. Formula verified against a known real result:
-- Nikola Jokić vs Phoenix, 3/7/25 (31 pts, 21 reb, 22 ast, 3 stl, 0 blk,
-- 4 tov, 13/22 fg, 2/3 ft, 3 fg3m) computes to exactly 113.1, matching the
-- confirmed actual Sleeper score of 113.10.
--
-- Double-double / triple-double logic: a "double-double" is 2+ of
-- {pts, reb, ast, stl, blk} >= 10; a "triple-double" is 3+ of the same
-- categories >= 10. CONFIRMED (via the Jokić test case) that these bonuses
-- STACK — a triple-double game gets both the +3 double-double bonus AND
-- the +5 triple-double bonus (+8 total), not one replacing the other.
--
-- Known limitation, accepted: technical/flagrant foul penalties (-2 each)
-- are NOT included, since game_logs has no columns distinguishing them
-- from ordinary personal fouls (nba_api's basic box score doesn't split
-- these out). Games with a technical/flagrant will score slightly higher
-- here than the real Sleeper result by 2 points per such foul.

DROP VIEW IF EXISTS game_fantasy_scores;

CREATE VIEW game_fantasy_scores AS
WITH stat_lines AS (
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
        (oreb + dreb) AS reb,
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
        -- Count how many of the 5 core categories hit double digits
        (
            (pts >= 10)::INT +
            ((oreb + dreb) >= 10)::INT +
            (ast >= 10)::INT +
            (stl >= 10)::INT +
            (blk >= 10)::INT
        ) AS double_digit_categories
    FROM game_logs
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
        0.5  * pts
      + 1.5  * reb
      + 0.5  * oreb
      + 2    * ast
      + 3    * stl
      + 3    * blk
      - 1    * tov
      + 1    * fgm
      - 0.45 * fga
      + 1    * ftm
      - 0.5  * fta
      + 0.5  * fg3m
      + CASE WHEN double_digit_categories >= 2 THEN 3 ELSE 0 END   -- double-double bonus
      + CASE WHEN double_digit_categories >= 3 THEN 5 ELSE 0 END   -- triple-double bonus (stacks)
      + CASE WHEN pts >= 40 THEN 2 ELSE 0 END                       -- 40+ points bonus
      + CASE WHEN pts >= 50 THEN 2 ELSE 0 END                       -- 50+ points bonus (stacks)
      + CASE WHEN ast >= 15 THEN 1 ELSE 0 END                       -- 15+ assists bonus
      + CASE WHEN reb >= 20 THEN 1 ELSE 0 END                       -- 20+ rebounds bonus
    , 2) AS fantasy_score
FROM stat_lines;

-- =========================
-- Verification
-- =========================

-- Row count should match game_logs exactly — no filtering, just added columns
SELECT COUNT(*) FROM game_fantasy_scores;
SELECT COUNT(*) FROM game_logs;

-- The ground-truth test case — should return exactly 113.10
SELECT p.full_name, gfs.game_date, gfs.pts, gfs.reb, gfs.ast, gfs.stl, gfs.blk,
       gfs.is_double_double, gfs.is_triple_double, gfs.fantasy_score
FROM game_fantasy_scores gfs
JOIN players p ON p.player_id = gfs.player_id
WHERE p.full_name ILIKE '%joki%' AND gfs.game_date = '2025-03-07';

-- Sanity check the overall distribution — min/max/avg fantasy score across
-- everyone, useful gut-check before trusting this for the benchmark layer
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
