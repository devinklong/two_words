-- Bivariate exploration for pace/off_rating/def_rating, using Postgres'
-- CORR() for single pairs (the full multi-pair matrix lives in
-- correlation_matrix_and_clustering.py instead). Runs against
-- team_stats_all_granularities unless noted.


-- =========================
-- 1. Pace vs. off_rating / def_rating -- confound check.
-- =========================
SELECT
    granularity,
    ROUND(CORR(pace, off_rating)::NUMERIC, 3) AS corr_pace_off_rating,
    ROUND(CORR(pace, def_rating)::NUMERIC, 3) AS corr_pace_def_rating,
    COUNT(*) AS n
FROM team_stats_all_granularities
WHERE pace IS NOT NULL AND off_rating IS NOT NULL AND def_rating IS NOT NULL
GROUP BY granularity
ORDER BY granularity;


-- =========================
-- 2. Off_rating vs. def_rating -- do two-way-good/two-way-bad teams cluster?
-- =========================
SELECT
    granularity,
    ROUND(CORR(off_rating, def_rating)::NUMERIC, 3) AS corr_off_def,
    COUNT(*) AS n
FROM team_stats_all_granularities
WHERE off_rating IS NOT NULL AND def_rating IS NOT NULL
GROUP BY granularity
ORDER BY granularity;

-- Quadrant breakdown by season median (latest season_to_date snapshot per team).
WITH latest AS (
    SELECT DISTINCT ON (team_id, season_id)
        team_id, season_id, off_rating, def_rating
    FROM team_rolling_season_to_date_stats
    WHERE games_included > 0
    ORDER BY team_id, season_id, game_date DESC
),
medians AS (
    SELECT season_id,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY off_rating) AS med_off,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY def_rating) AS med_def
    FROM latest
    GROUP BY season_id
)
SELECT
    l.season_id, l.team_id, l.off_rating, l.def_rating,
    CASE WHEN l.off_rating >= m.med_off THEN 'good_off' ELSE 'bad_off' END AS off_bucket,
    CASE WHEN l.def_rating <= m.med_def THEN 'good_def' ELSE 'bad_def' END AS def_bucket
    -- <= median = good_def, since lower def_rating is better defense.
FROM latest l
JOIN medians m ON m.season_id = l.season_id
ORDER BY l.season_id, off_bucket, def_bucket;


-- =========================
-- 3. Stat vs. games_included -- noisier at low sample sizes (early season)?
-- =========================
WITH bucketed AS (
    SELECT
        granularity,
        CASE
            WHEN games_included IS NULL THEN 'n/a (single_game)'
            WHEN games_included < 5 THEN '0-4 games'
            WHEN games_included < 10 THEN '5-9 games'
            WHEN games_included < 20 THEN '10-19 games'
            ELSE '20+ games'
        END AS sample_bucket,
        off_rating
    FROM team_stats_all_granularities
    WHERE granularity IN ('season_to_date', 'trailing_10') AND games_included IS NOT NULL
)
SELECT
    granularity, sample_bucket,
    ROUND(AVG(off_rating)::NUMERIC, 2) AS avg_off_rating,
    ROUND(STDDEV(off_rating)::NUMERIC, 2) AS stddev_off_rating,
    COUNT(*) AS n
FROM bucketed
GROUP BY granularity, sample_bucket
ORDER BY granularity,
    CASE sample_bucket
        WHEN '0-4 games' THEN 1 WHEN '5-9 games' THEN 2
        WHEN '10-19 games' THEN 3 ELSE 4
    END;
-- A meaningfully higher stddev in low-sample buckets argues for a minimum-games floor.


-- =========================
-- 4. Stat vs. is_home
-- =========================
SELECT
    granularity, is_home,
    ROUND(AVG(pace)::NUMERIC, 2) AS avg_pace,
    ROUND(AVG(off_rating)::NUMERIC, 2) AS avg_off_rating,
    ROUND(AVG(def_rating)::NUMERIC, 2) AS avg_def_rating,
    COUNT(*) AS n
FROM team_stats_all_granularities
GROUP BY granularity, is_home
ORDER BY granularity, is_home DESC;
-- A real is_home gap not already accounted for elsewhere is worth knowing about.


-- =========================
-- 5. Stat vs. rest/B2B status, joined via team_schedule_b2b_flags.
-- =========================
SELECT
    g.granularity,
    b2b.is_second_night_of_b2b,
    ROUND(AVG(g.pace)::NUMERIC, 2) AS avg_pace,
    ROUND(AVG(g.off_rating)::NUMERIC, 2) AS avg_off_rating,
    ROUND(AVG(g.def_rating)::NUMERIC, 2) AS avg_def_rating,
    COUNT(*) AS n
FROM team_stats_all_granularities g
JOIN team_schedule_b2b_flags b2b
    ON b2b.team_id = g.team_id
    AND b2b.season_id = g.season_id
    AND b2b.game_date = g.game_date
GROUP BY g.granularity, b2b.is_second_night_of_b2b
ORDER BY g.granularity, b2b.is_second_night_of_b2b;
-- A real gap here is a second independent confirmation of v1.1's player-level B2B effect.

-- Same, but on the OPPONENT's B2B status -- does facing a tired team change YOUR output?
SELECT
    opp_b2b.is_second_night_of_b2b AS opponent_is_second_night_of_b2b,
    ROUND(AVG(tvo.own_pace)::NUMERIC, 2) AS avg_own_pace,
    ROUND(AVG(tvo.own_off_rating)::NUMERIC, 2) AS avg_own_off_rating,
    ROUND(AVG(tvo.own_def_rating)::NUMERIC, 2) AS avg_own_def_rating,
    COUNT(*) AS n
FROM team_vs_opponent_trailing10 tvo
JOIN team_schedule_b2b_flags opp_b2b
    ON opp_b2b.team_id = tvo.opponent_team_id
    AND opp_b2b.season_id = tvo.season_id
    AND opp_b2b.game_date = tvo.game_date
GROUP BY opp_b2b.is_second_night_of_b2b
ORDER BY opp_b2b.is_second_night_of_b2b;
