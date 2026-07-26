-- Calculates total rebounds on the fly from OREB + DREB,
-- avoiding a stored derived/transitive-dependency column (3NF).
CREATE VIEW game_logs_with_totals AS
SELECT
    *,
    OREB + DREB AS reb
FROM game_logs;
