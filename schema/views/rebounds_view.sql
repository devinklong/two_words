-- Recomputes total rebounds (oreb + dreb) from game_logs, per the project's
-- 3NF rule: don't store a derived total alongside its components.

DROP VIEW IF EXISTS rebounds_view;

CREATE VIEW rebounds_view AS
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
    fgm,
    fga,
    fg3m,
    fg3a,
    ftm,
    fta,
    oreb,
    dreb,
    (oreb + dreb) AS reb,
    ast,
    stl,
    blk,
    tov,
    pf,
    pts,
    plus_minus
FROM game_logs;

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM rebounds_view;
SELECT COUNT(*) FROM game_logs;

-- Spot check against Raynaud's known first row (2 OREB + 7 DREB = 9 REB)
SELECT player_id, game_date, oreb, dreb, reb
FROM rebounds_view
WHERE player_id = 1642875
ORDER BY game_date DESC
LIMIT 5;

-- No NULL rebound totals should exist, since oreb/dreb are always populated
-- for a logged game (unlike shooting attempts, which can legitimately be 0)
SELECT COUNT(*) FROM rebounds_view WHERE reb IS NULL;
