-- Calculates shooting percentages on the fly from raw counts,
-- avoiding stored derived/transitive-dependency columns (3NF).
CREATE VIEW game_logs_with_pct AS
SELECT
    *,
    ROUND(FGM::numeric / NULLIF(FGA, 0), 3) AS fg_pct,
    ROUND(FG3M::numeric / NULLIF(FG3A, 0), 3) AS fg3_pct,
    ROUND(FTM::numeric / NULLIF(FTA, 0), 3) AS ft_pct
FROM game_logs;
