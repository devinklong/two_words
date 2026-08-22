-- Defines the ~150-210 player-season "ownable pool": mean + 1.25*stddev >= 35
-- (ceiling-based, not raw average, so spike-capable players aren't missed —
-- see methodology_notes.md's "Tobias Harris problem"). k and threshold are
-- kept as literals here deliberately, not buried, since they're first-pass
-- values Phase 2's backtest is the mechanism to revise, not this file.
--
-- BOOTSTRAP LOGIC ADDED 8/22/26: uses player_season_fantasy_stats (the
-- validated, backtested methodology) for any player with >=20 games
-- logged in the CURRENT season (read from current_season_config, not
-- hardcoded -- see schema/tables/create_tables.sql); falls back to a
-- rolling last-20-real-games window (any season/team boundary) ONLY
-- for players who haven't hit that threshold yet this season. Not a
-- permanent replacement for season-bound stats -- the validated
-- +1.71/+1.72 backtest edge was proven against
-- player_season_fantasy_stats specifically, and by midseason nearly
-- every player has enough games to use it; the rolling window only
-- patches the real gap (start of a new season, or an individual
-- player who's missed enough time to still be short -- team_id is
-- deliberately never filtered on inside it, matching the confirmed
-- finding that team-level effects don't hold up as a signal,
-- architecture_risks.md v2.0). Consolidated directly into this file
-- rather than a separate view -- game_lock_signal.sql already gets
-- avg/stddev by joining THIS view, so there's no second file that
-- needs to know about the bootstrap logic at all.
--
-- Backtest/grid-search scripts still query player_season_fantasy_stats
-- directly and are completely unaffected by any of this.

DROP VIEW IF EXISTS ownable_player_pool CASCADE;

CREATE VIEW ownable_player_pool AS
WITH current_season_stats AS (
    -- Players with a real, validated season-long profile already --
    -- the default source once available.
    SELECT
        player_id,
        season_id AS current_season_id,
        avg_fantasy_score,
        stddev_fantasy_score,
        'season' AS stats_source
    FROM player_season_fantasy_stats
    WHERE season_id = (SELECT season_id FROM current_season_config WHERE id = 1)
      AND games_played >= 20
),
ranked_games AS (
    SELECT
        gfs.player_id,
        gfs.game_date,
        gfs.fantasy_score,
        FIRST_VALUE(gfs.season_id) OVER (
            PARTITION BY gfs.player_id ORDER BY gfs.game_date DESC
        ) AS current_season_id,
        ROW_NUMBER() OVER (
            PARTITION BY gfs.player_id ORDER BY gfs.game_date DESC
        ) AS games_ago
    FROM game_fantasy_scores gfs
    -- bounded lookback: only the two most recent real seasons are
    -- eligible, mirroring get_spike_profile()'s one-year fallback cap
    WHERE gfs.season_id IN (
        SELECT DISTINCT season_id FROM game_fantasy_scores
        ORDER BY season_id DESC LIMIT 2
    )
),
rolling_stats AS (
    -- Fallback ONLY -- computed for everyone, but only actually used
    -- below for players current_season_stats didn't already cover.
    SELECT
        player_id,
        current_season_id,
        ROUND(AVG(fantasy_score), 2) AS avg_fantasy_score,
        ROUND(STDDEV(fantasy_score), 2) AS stddev_fantasy_score,
        'rolling' AS stats_source
    FROM ranked_games
    WHERE games_ago <= 20
    GROUP BY player_id, current_season_id
    HAVING COUNT(*) >= 20
),
bootstrapped AS (
    SELECT * FROM current_season_stats
    UNION ALL
    SELECT r.player_id, r.current_season_id, r.avg_fantasy_score,
           r.stddev_fantasy_score, r.stats_source
    FROM rolling_stats r
    WHERE NOT EXISTS (
        SELECT 1 FROM current_season_stats c WHERE c.player_id = r.player_id
    )
)
SELECT
    player_id,
    current_season_id AS season_id,
    avg_fantasy_score,
    stddev_fantasy_score,
    stats_source,
    ROUND(avg_fantasy_score + 1.25 * stddev_fantasy_score, 2) AS eligibility_ceiling
FROM bootstrapped
WHERE avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35;

-- =========================
-- Verification
-- =========================

-- Expect ~150-210
SELECT COUNT(*) AS pool_size FROM ownable_player_pool;

-- How many players are on each source right now -- expect mostly
-- 'rolling' at the very start of a new season, shifting almost
-- entirely to 'season' by midseason. A LARGE 'rolling' count deep
-- into the season would be worth investigating (are current-season
-- games actually being ingested daily?), not expected as normal.
SELECT stats_source, COUNT(*) AS player_count
FROM ownable_player_pool
GROUP BY stats_source
ORDER BY stats_source;

-- Jokić should trivially clear
SELECT opp.*, p.full_name
FROM ownable_player_pool opp
JOIN players p ON p.player_id = opp.player_id
WHERE p.full_name ILIKE '%joki%';
