-- Flags back-to-back games directly from team_schedule: a game is the
-- "second night" of a B2B if the same team's previous game was exactly 1
-- day earlier; it's the "first night" of a B2B if the team's NEXT game is
-- exactly 1 day later. A game can be neither, one, or (rare, 3-in-3
-- scenarios) arguably both from different angles — tracked separately
-- rather than collapsed into one flag, since first-night and second-night
-- effects are plausibly different (rest-management before vs. fatigue
-- during) and shouldn't be assumed identical without checking.

DROP VIEW IF EXISTS team_schedule_b2b_flags;

CREATE VIEW team_schedule_b2b_flags AS
SELECT
    ts.*,
    (ts.game_date - LAG(ts.game_date) OVER (
        PARTITION BY ts.team_id, ts.season_id ORDER BY ts.game_date
    )) = 1 AS is_second_night_of_b2b,
    (LEAD(ts.game_date) OVER (
        PARTITION BY ts.team_id, ts.season_id ORDER BY ts.game_date
    ) - ts.game_date) = 1 AS is_first_night_of_b2b
FROM team_schedule ts;

-- =========================
-- The actual comparison: real fantasy_score outcomes, B2B vs not
-- =========================

SELECT
    CASE
        WHEN b2b.is_second_night_of_b2b THEN 'second_night_of_b2b'
        WHEN b2b.is_first_night_of_b2b THEN 'first_night_of_b2b'
        ELSE 'normal_rest'
    END AS game_type,
    COUNT(*) AS game_count,
    ROUND(AVG(gfs.fantasy_score), 2) AS avg_fantasy_score,
    ROUND(STDDEV_SAMP(gfs.fantasy_score), 2) AS stddev_fantasy_score,
    ROUND(AVG(gfs.minutes), 2) AS avg_minutes
FROM game_fantasy_scores gfs
JOIN team_schedule_b2b_flags b2b
    ON b2b.game_id = gfs.game_id AND b2b.team_id = gfs.team_id
GROUP BY
    CASE
        WHEN b2b.is_second_night_of_b2b THEN 'second_night_of_b2b'
        WHEN b2b.is_first_night_of_b2b THEN 'first_night_of_b2b'
        ELSE 'normal_rest'
    END
ORDER BY avg_fantasy_score DESC;

-- =========================
-- Verification / sanity checks
-- =========================

-- How many total B2B occurrences exist across the dataset — rough gut
-- check against known NBA scheduling norms (a normal season has meaningful
-- B2B counts per team, this shouldn't be near-zero or absurdly high)
SELECT
    SUM(is_second_night_of_b2b::INT) AS total_b2b_second_nights,
    SUM(is_first_night_of_b2b::INT) AS total_b2b_first_nights
FROM team_schedule_b2b_flags;

-- Spot check one team's actual back-to-back stretch by eye — pick any
-- team_id and season, confirm date gaps of exactly 1 day line up with the
-- flags
SELECT team_id, season_id, game_date, is_second_night_of_b2b, is_first_night_of_b2b
FROM team_schedule_b2b_flags
WHERE team_id = 1610612758 AND season_id = '22024'
ORDER BY game_date
LIMIT 20;

-- Does this effect show up specifically for HIGH-usage players (the ones
-- this tool actually cares about), or is it diluted by bench players who
-- weren't playing heavy minutes anyway? Restrict to player-seasons with a
-- season avg fantasy_score >= 30 as a rough "meaningful role" filter.
SELECT
    CASE
        WHEN b2b.is_second_night_of_b2b THEN 'second_night_of_b2b'
        WHEN b2b.is_first_night_of_b2b THEN 'first_night_of_b2b'
        ELSE 'normal_rest'
    END AS game_type,
    COUNT(*) AS game_count,
    ROUND(AVG(gfs.fantasy_score), 2) AS avg_fantasy_score,
    ROUND(AVG(gfs.minutes), 2) AS avg_minutes
FROM game_fantasy_scores gfs
JOIN team_schedule_b2b_flags b2b
    ON b2b.game_id = gfs.game_id AND b2b.team_id = gfs.team_id
JOIN player_season_fantasy_stats pss
    ON pss.player_id = gfs.player_id AND pss.season_id = gfs.season_id
WHERE pss.avg_fantasy_score >= 30
GROUP BY
    CASE
        WHEN b2b.is_second_night_of_b2b THEN 'second_night_of_b2b'
        WHEN b2b.is_first_night_of_b2b THEN 'first_night_of_b2b'
        ELSE 'normal_rest'
    END
ORDER BY avg_fantasy_score DESC;
