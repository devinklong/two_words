-- Defines the ~150-210 player-season "ownable pool": mean + 1.25*stddev >= 35
-- (ceiling-based, not raw average, so spike-capable players aren't missed —
-- see methodology_notes.md's "Tobias Harris problem"). k and threshold are
-- kept as literals here deliberately, not buried, since they're first-pass
-- values Phase 2's backtest is the mechanism to revise, not this file.
--
-- BOOTSTRAP LOGIC (added 8/22/26, CORRECTED 8/23/26 — see below): uses
-- player_season_fantasy_stats (the validated, backtested methodology) for
-- any player with >=20 games logged in a season; falls back to a rolling
-- last-20-real-games window (any season/team boundary) ONLY for players
-- who don't have that yet for the CURRENT live season specifically
-- (read from current_season_config, not hardcoded).
--
-- FIXED 8/23/26 (real bug, caught by a rebuild_lock_pipeline.py
-- regression check, not by anything in the original #9 verification):
-- the first version of this bootstrap filtered current_season_stats to
-- ONLY the current season_id — which meant every OTHER season silently
-- got ZERO rows in this view, since rolling_stats also only ever
-- computed the live bootstrap window, never anything historical. That
-- collapsed game_lock_signal (which joins this view) down to roughly
-- ONE season's worth of data total, across the whole 5-season history —
-- badly breaking weekly_outcome_simulation.sql and every backtest/
-- regression check that depends on game_lock_signal covering multiple
-- seasons. Root cause: the #9 fix was only ever meant to ADD a rolling
-- fallback for the current season on top of full historical coverage,
-- not REPLACE historical coverage entirely. Fixed below: historical
-- seasons (anything != current_season_config's season_id) are now
-- restored to reading player_season_fantasy_stats directly, exactly as
-- this view worked before #9 — completely unaffected by any bootstrap
-- logic. The rolling-window fallback is now correctly scoped to apply
-- ONLY within the current live season.
--
-- Deliberately NOT a permanent replacement for season-bound stats even
-- for the current season -- the validated +1.71/+1.72 backtest edge was
-- proven against player_season_fantasy_stats specifically, and by
-- midseason nearly every player has 20+ games and is back on that exact
-- validated source. The rolling window only patches the real gap (start
-- of a new season, or an individual player who's missed enough time to
-- still be short -- team_id is deliberately never filtered on inside it,
-- matching the confirmed finding that team-level effects don't hold up
-- as a signal, architecture_risks.md v2.0).
--
-- Consolidated directly into this file rather than a separate view --
-- game_lock_signal.sql already joins THIS view and gets avg/stddev from
-- it, so there's no second file that needs to know about the bootstrap
-- logic at all. Backtest/grid-search scripts still query
-- player_season_fantasy_stats directly and are completely unaffected by
-- any of this.

DROP VIEW IF EXISTS ownable_player_pool CASCADE;

CREATE VIEW ownable_player_pool AS
WITH current_season_stats AS (
    -- Players with a real, validated season-long profile for the
    -- CURRENT live season specifically -- the default source once
    -- available for that season.
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
    -- Fallback ONLY for the current live season -- computed for
    -- everyone, but only actually used below for players
    -- current_season_stats didn't already cover for the CURRENT
    -- season specifically.
    SELECT
        player_id,
        current_season_id,
        ROUND(AVG(fantasy_score), 2) AS avg_fantasy_score,
        ROUND(STDDEV(fantasy_score), 2) AS stddev_fantasy_score,
        'rolling' AS stats_source
    FROM ranked_games
    WHERE games_ago <= 20
      AND current_season_id = (SELECT season_id FROM current_season_config WHERE id = 1)
    GROUP BY player_id, current_season_id
    HAVING COUNT(*) >= 20
),
current_season_bootstrapped AS (
    SELECT * FROM current_season_stats
    UNION ALL
    SELECT r.player_id, r.current_season_id, r.avg_fantasy_score,
           r.stddev_fantasy_score, r.stats_source
    FROM rolling_stats r
    WHERE NOT EXISTS (
        SELECT 1 FROM current_season_stats c WHERE c.player_id = r.player_id
    )
),
-- RESTORED 8/23/26: every season OTHER than the current live one reads
-- player_season_fantasy_stats directly, exactly as this view worked
-- before the #9 bootstrap was added -- completely unaffected by any of
-- the bootstrap logic above.
historical_seasons AS (
    SELECT
        player_id,
        season_id AS current_season_id,
        avg_fantasy_score,
        stddev_fantasy_score,
        'season' AS stats_source
    FROM player_season_fantasy_stats
    WHERE season_id != (SELECT season_id FROM current_season_config WHERE id = 1)
      AND games_played >= 20
),
all_seasons AS (
    SELECT * FROM historical_seasons
    UNION ALL
    SELECT * FROM current_season_bootstrapped
)
SELECT
    player_id,
    current_season_id AS season_id,
    avg_fantasy_score,
    stddev_fantasy_score,
    stats_source,
    ROUND(avg_fantasy_score + 1.25 * stddev_fantasy_score, 2) AS eligibility_ceiling
FROM all_seasons
WHERE avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35;

-- =========================
-- Verification
-- =========================

-- Should now show a real row for EVERY season with data, not just the
-- current one -- this is the check that would have caught the bug
SELECT season_id, COUNT(*) AS pool_size
FROM ownable_player_pool
GROUP BY season_id
ORDER BY season_id;

-- How many players are on each source right now, CURRENT season only
-- (historical seasons are always 'season', by construction)
SELECT stats_source, COUNT(*) AS player_count
FROM ownable_player_pool
WHERE season_id = (SELECT season_id FROM current_season_config WHERE id = 1)
GROUP BY stats_source
ORDER BY stats_source;

-- Jokić should trivially clear every season he has data for
SELECT opp.*, p.full_name
FROM ownable_player_pool opp
JOIN players p ON p.player_id = opp.player_id
WHERE p.full_name ILIKE '%joki%'
ORDER BY opp.season_id;
