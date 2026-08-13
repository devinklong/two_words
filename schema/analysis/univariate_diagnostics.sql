-- Univariate exploration for pace/off_rating/def_rating (net_rating
-- excluded -- it's off_rating minus def_rating by definition, not
-- independent). Runs against team_stats_all_granularities so every
-- query covers all three granularities at once; exploration queries,
-- not permanent views -- run sections as needed.


-- =========================
-- 1. Distribution shape: mean/median/stddev/IQR per metric per granularity
-- =========================
SELECT
    granularity,
    'pace' AS metric,
    COUNT(*) AS n,
    ROUND(AVG(pace)::NUMERIC, 2) AS mean,
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pace)::NUMERIC, 2) AS median,
    ROUND(STDDEV(pace)::NUMERIC, 2) AS stddev,
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY pace)::NUMERIC, 2) AS p25,
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY pace)::NUMERIC, 2) AS p75,
    ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY pace)
         - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY pace))::NUMERIC, 2) AS iqr
FROM team_stats_all_granularities
WHERE pace IS NOT NULL
GROUP BY granularity

UNION ALL

SELECT
    granularity, 'off_rating' AS metric, COUNT(*),
    ROUND(AVG(off_rating)::NUMERIC, 2),
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY off_rating)::NUMERIC, 2),
    ROUND(STDDEV(off_rating)::NUMERIC, 2),
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY off_rating)::NUMERIC, 2),
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY off_rating)::NUMERIC, 2),
    ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY off_rating)
         - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY off_rating))::NUMERIC, 2)
FROM team_stats_all_granularities
WHERE off_rating IS NOT NULL
GROUP BY granularity

UNION ALL

SELECT
    granularity, 'def_rating' AS metric, COUNT(*),
    ROUND(AVG(def_rating)::NUMERIC, 2),
    ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY def_rating)::NUMERIC, 2),
    ROUND(STDDEV(def_rating)::NUMERIC, 2),
    ROUND(PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY def_rating)::NUMERIC, 2),
    ROUND(PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY def_rating)::NUMERIC, 2),
    ROUND((PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY def_rating)
         - PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY def_rating))::NUMERIC, 2)
FROM team_stats_all_granularities
WHERE def_rating IS NOT NULL
GROUP BY granularity

ORDER BY metric, granularity;
-- EXPECT: stddev(single_game) > stddev(trailing_10) > stddev(season_to_date) for every metric.


-- =========================
-- 2. Outlier detection: rows more than 3 stddev from that granularity's mean
-- =========================
WITH stats AS (
    SELECT granularity,
           AVG(pace) AS mean_pace, STDDEV(pace) AS sd_pace,
           AVG(off_rating) AS mean_off, STDDEV(off_rating) AS sd_off,
           AVG(def_rating) AS mean_def, STDDEV(def_rating) AS sd_def
    FROM team_stats_all_granularities
    GROUP BY granularity
)
SELECT g.game_id, g.team_id, g.granularity, g.game_date,
       g.pace, g.off_rating, g.def_rating,
       ROUND((ABS(g.pace - s.mean_pace) / NULLIF(s.sd_pace, 0))::NUMERIC, 2) AS pace_z,
       ROUND((ABS(g.off_rating - s.mean_off) / NULLIF(s.sd_off, 0))::NUMERIC, 2) AS off_z,
       ROUND((ABS(g.def_rating - s.mean_def) / NULLIF(s.sd_def, 0))::NUMERIC, 2) AS def_z
FROM team_stats_all_granularities g
JOIN stats s ON s.granularity = g.granularity
WHERE ABS(g.pace - s.mean_pace) / NULLIF(s.sd_pace, 0) > 3
   OR ABS(g.off_rating - s.mean_off) / NULLIF(s.sd_off, 0) > 3
   OR ABS(g.def_rating - s.mean_def) / NULLIF(s.sd_def, 0) > 3
ORDER BY g.granularity, pace_z DESC NULLS LAST;
-- Eyeball each hit: real blowout/OT-fest (keep) vs. pipeline glitch (investigate).


-- =========================
-- 3. League-wide trend over the season (monthly), single_game only
-- =========================
SELECT
    season_id,
    DATE_TRUNC('month', game_date)::DATE AS month,
    ROUND(AVG(pace)::NUMERIC, 2) AS league_avg_pace,
    ROUND(AVG(off_rating)::NUMERIC, 2) AS league_avg_off_rating,
    COUNT(*) AS n_team_games
FROM team_stats_all_granularities
WHERE granularity = 'single_game'
GROUP BY season_id, DATE_TRUNC('month', game_date)
ORDER BY season_id, month;
-- Watch for October-to-April drift, which would make November and March numbers non-comparable.


-- =========================
-- 4. Team percentile ranking within a season (final season_to_date snapshot per team)
-- =========================
WITH latest_per_team_season AS (
    SELECT DISTINCT ON (team_id, season_id)
        team_id, season_id, pace, off_rating, def_rating
    FROM team_rolling_season_to_date_stats
    WHERE games_included > 0
    ORDER BY team_id, season_id, game_date DESC
)
SELECT
    season_id, team_id, pace, off_rating, def_rating,
    ROUND((100 * PERCENT_RANK() OVER (PARTITION BY season_id ORDER BY pace))::NUMERIC, 1) AS pace_pctile,
    ROUND((100 * PERCENT_RANK() OVER (PARTITION BY season_id ORDER BY off_rating))::NUMERIC, 1) AS off_rating_pctile,
    ROUND((100 * PERCENT_RANK() OVER (PARTITION BY season_id ORDER BY def_rating DESC))::NUMERIC, 1) AS def_rating_pctile
    -- DESC: lower def_rating (better defense) should mean a higher percentile.
FROM latest_per_team_season
ORDER BY season_id, off_rating_pctile DESC;


-- =========================
-- 5. Season-over-season stability across the 5 backfilled seasons
-- =========================
WITH season_final AS (
    SELECT DISTINCT ON (team_id, season_id)
        team_id, season_id, pace, off_rating, def_rating
    FROM team_rolling_season_to_date_stats
    WHERE games_included > 0
    ORDER BY team_id, season_id, game_date DESC
)
SELECT
    team_id,
    COUNT(DISTINCT season_id) AS seasons_present,
    ROUND(AVG(pace)::NUMERIC, 2) AS avg_pace_across_seasons,
    ROUND(STDDEV(pace)::NUMERIC, 2) AS stddev_pace_across_seasons,
    ROUND(AVG(off_rating)::NUMERIC, 2) AS avg_off_rating_across_seasons,
    ROUND(STDDEV(off_rating)::NUMERIC, 2) AS stddev_off_rating_across_seasons
FROM season_final
GROUP BY team_id
ORDER BY stddev_off_rating_across_seasons DESC NULLS LAST;
-- High stddev = team identity shifted a lot season-to-season (roster turnover, scheme change).


-- =========================
-- 6. Volatility within a season: how much does a team's trailing-10 window swing?
-- =========================
SELECT
    team_id, season_id,
    ROUND(STDDEV(pace)::NUMERIC, 2) AS pace_volatility,
    ROUND(STDDEV(off_rating)::NUMERIC, 2) AS off_rating_volatility,
    ROUND(STDDEV(def_rating)::NUMERIC, 2) AS def_rating_volatility,
    COUNT(*) AS games_with_full_window
FROM team_rolling_trailing10_advanced_stats
WHERE games_included = 10  -- fully-populated windows only, for apples-to-apples volatility
GROUP BY team_id, season_id
ORDER BY off_rating_volatility DESC NULLS LAST;
-- High volatility teams are streakier, which may itself be a useful feature later.


-- =========================
-- 7. Season-to-date vs. trailing-10 divergence
-- =========================
SELECT
    rss.team_id, rss.season_id, rss.game_date,
    rss.games_included AS std_games_included, rss.pace AS std_pace,
    t10.games_included AS t10_games_included, t10.pace AS t10_pace,
    ROUND((t10.pace - rss.pace)::NUMERIC, 2) AS pace_gap,
    ROUND((t10.off_rating - rss.off_rating)::NUMERIC, 2) AS off_rating_gap,
    ROUND((t10.def_rating - rss.def_rating)::NUMERIC, 2) AS def_rating_gap
FROM team_rolling_season_to_date_stats rss
JOIN team_rolling_trailing10_advanced_stats t10
    ON t10.game_id = rss.game_id AND t10.team_id = rss.team_id
WHERE rss.games_included >= 10 AND t10.games_included = 10  -- both windows fully populated
ORDER BY ABS(t10.off_rating - rss.off_rating) DESC
LIMIT 50;
-- Rows near the top: team is currently hot/cold relative to its own season baseline.
