-- Recomputes shooting percentages from game_logs' atomic makes/attempts
-- columns, per the project's 3NF rule: don't store derived values, recreate
-- them via views instead. NULLIF prevents divide-by-zero on 0-attempt rows
-- (e.g. a player who took no 3-pointers that game) — those come back NULL
-- rather than erroring.

DROP VIEW IF EXISTS percentages_view;

CREATE VIEW percentages_view AS
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
    ROUND(fgm::NUMERIC / NULLIF(fga, 0), 3) AS fg_pct,
    fg3m,
    fg3a,
    ROUND(fg3m::NUMERIC / NULLIF(fg3a, 0), 3) AS fg3_pct,
    ftm,
    fta,
    ROUND(ftm::NUMERIC / NULLIF(fta, 0), 3) AS ft_pct,
    oreb,
    dreb,
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

-- Row count should match game_logs exactly — this is a 1:1 view, no filtering
SELECT COUNT(*) FROM percentages_view;
SELECT COUNT(*) FROM game_logs;

-- Spot check against Raynaud's known first row (37 min, 7/10 FGM/FGA = 0.700)
SELECT player_id, game_date, fgm, fga, fg_pct, fg3m, fg3a, fg3_pct, ftm, fta, ft_pct
FROM percentages_view
WHERE player_id = 1642875
ORDER BY game_date DESC
LIMIT 5;

-- Confirm 0-attempt rows come back NULL, not an error or a 0
SELECT COUNT(*) AS zero_fg3a_rows
FROM percentages_view
WHERE fg3a = 0 AND fg3_pct IS NULL;
