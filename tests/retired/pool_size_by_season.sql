-- Exact ownable-pool player counts per season, split into the Phase 2
-- train/test groups (train: 2021-24, validate: 2024-26). Anyone picking
-- up the Phase 2 backtest should run this first to know exactly how many
-- player-seasons they're calibrating/validating against, rather than
-- relying on the ~150-210/season estimate from methodology_notes.md.

SELECT
    season_id,
    CASE
        WHEN season_id IN ('22021', '22022', '22023') THEN 'train'
        WHEN season_id IN ('22024', '22025') THEN 'validate'
    END AS split,
    COUNT(*) AS pool_size
FROM ownable_player_pool
GROUP BY season_id
ORDER BY season_id;

-- Totals per split — the numbers that actually matter for calibration
SELECT
    CASE
        WHEN season_id IN ('22021', '22022', '22023') THEN 'train (2021-24)'
        WHEN season_id IN ('22024', '22025') THEN 'validate (2024-26)'
    END AS split,
    COUNT(*) AS total_player_seasons,
    COUNT(DISTINCT player_id) AS distinct_players
FROM ownable_player_pool
GROUP BY 1
ORDER BY 1;
