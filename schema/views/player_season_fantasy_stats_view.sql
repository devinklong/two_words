-- Per-player, per-season fantasy score statistics, built on top of the
-- FULL NBA player universe (not yet scoped to this league's owned/rostered
-- pool — that requires Sleeper roster data, not yet integrated). Season-
-- scoped rather than career-long, since a player's role/usage can change
-- meaningfully year to year (trades, injuries, aging curves) — a career
-- average would blur together very different eras of the same player.
--
-- This is groundwork for both layers of the lock/hold model:
--   - League-relative layer: eventually needs re-scoping to just the
--     ~160-210 players actually ownable in this league (10 teams x 16
--     active roster spots, plus IR/taxi slots that don't score), not the
--     full ~500-540 active players per season used here.
--   - Player-relative layer: this view's avg/stddev per player IS the
--     direct input for testing the "+1 stdev above mean" lock threshold
--     hypothesis, regardless of league ownership scope.

DROP VIEW IF EXISTS player_season_fantasy_stats;

CREATE VIEW player_season_fantasy_stats AS
SELECT
    gfs.player_id,
    p.full_name,
    gfs.season_id,
    COUNT(*) AS games_played,
    ROUND(AVG(gfs.fantasy_score), 2) AS avg_fantasy_score,
    ROUND(STDDEV_SAMP(gfs.fantasy_score), 2) AS stddev_fantasy_score,
    ROUND(MIN(gfs.fantasy_score), 2) AS min_fantasy_score,
    ROUND(MAX(gfs.fantasy_score), 2) AS max_fantasy_score,
    ROUND(AVG(gfs.fantasy_score) + STDDEV_SAMP(gfs.fantasy_score), 2) AS mean_plus_1sd
FROM game_fantasy_scores gfs
JOIN players p ON p.player_id = gfs.player_id
GROUP BY gfs.player_id, p.full_name, gfs.season_id;

-- =========================
-- Verification
-- =========================

-- Row count sanity check — should be roughly (players per season) summed
-- across 5 seasons, NOT total game_logs rows (this is grouped, not 1:1)
SELECT COUNT(*) FROM player_season_fantasy_stats;

-- Spot check Jokić's 2024-25 season stats — single-digit games_played
-- would be a red flag, low 100s is expected for a full healthy season
SELECT * FROM player_season_fantasy_stats
WHERE full_name ILIKE '%joki%' AND season_id = '22024';

-- The population the user asked about — how many player-seasons average
-- BELOW 40 fantasy points/game (the pool this tool is actually meant to
-- serve, per the design philosophy) vs at/above it
SELECT
    (avg_fantasy_score < 40) AS below_40_avg,
    COUNT(*) AS player_season_count
FROM player_season_fantasy_stats
GROUP BY (avg_fantasy_score < 40);

-- Distribution eyeball — sorted by average, to see the shape of who's
-- actually a "star" (auto-lock territory) vs everyone else
SELECT full_name, season_id, games_played, avg_fantasy_score, stddev_fantasy_score, mean_plus_1sd
FROM player_season_fantasy_stats
WHERE games_played >= 20  -- filter out tiny sample sizes for this eyeball check
ORDER BY avg_fantasy_score DESC
LIMIT 20;
