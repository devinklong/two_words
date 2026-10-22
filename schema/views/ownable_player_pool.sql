-- Ownable pool: players whose spike (avg + 1.25 * stddev) clears 35, with the stats the lock
-- calculator uses. stats_source: 'season' (20+ games this season) or 'rolling' (last 20 games
-- across two seasons, until the current season reaches 20).
--
-- Team-change adjustment (added 10/22/26): a player in nba_player_movers whose stats are still
-- 'rolling' (the window is mostly old-team games) has avg and stddev multiplied by the mover
-- factor below (0.90). Membership in the pool is judged on the UNadjusted numbers so an
-- adjustment never removes someone from the pool. Once he has 20 games this season ('season'),
-- the adjustment stops by itself. The factor is a judgment call; change the 0.90 below.
CREATE OR REPLACE VIEW ownable_player_pool AS
WITH current_season_stats AS (
         SELECT player_season_fantasy_stats.player_id,
            player_season_fantasy_stats.season_id AS current_season_id,
            player_season_fantasy_stats.avg_fantasy_score,
            player_season_fantasy_stats.stddev_fantasy_score,
            'season'::text AS stats_source
           FROM player_season_fantasy_stats
          WHERE player_season_fantasy_stats.season_id::text = (( SELECT current_season_config.season_id
                   FROM current_season_config
                  WHERE current_season_config.id = 1)) AND player_season_fantasy_stats.games_played >= 20
        ), ranked_games AS (
         SELECT gfs.player_id,
            gfs.game_date,
            gfs.fantasy_score,
            first_value(gfs.season_id) OVER (PARTITION BY gfs.player_id ORDER BY gfs.game_date DESC) AS current_season_id,
            row_number() OVER (PARTITION BY gfs.player_id ORDER BY gfs.game_date DESC) AS games_ago
           FROM game_fantasy_scores gfs
          WHERE (gfs.season_id::text IN ( SELECT DISTINCT game_fantasy_scores.season_id
                   FROM game_fantasy_scores
                  ORDER BY game_fantasy_scores.season_id DESC
                 LIMIT 2))
        ), rolling_stats AS (
         SELECT ranked_games.player_id,
            ranked_games.current_season_id,
            round(avg(ranked_games.fantasy_score), 2) AS avg_fantasy_score,
            round(stddev(ranked_games.fantasy_score), 2) AS stddev_fantasy_score,
            'rolling'::text AS stats_source
           FROM ranked_games
          WHERE ranked_games.games_ago <= 20 AND ranked_games.current_season_id::text = (( SELECT current_season_config.season_id
                   FROM current_season_config
                  WHERE current_season_config.id = 1))
          GROUP BY ranked_games.player_id, ranked_games.current_season_id
         HAVING count(*) >= 20
        ), current_season_bootstrapped AS (
         SELECT current_season_stats.player_id,
            current_season_stats.current_season_id,
            current_season_stats.avg_fantasy_score,
            current_season_stats.stddev_fantasy_score,
            current_season_stats.stats_source
           FROM current_season_stats
        UNION ALL
         SELECT r.player_id,
            r.current_season_id,
            r.avg_fantasy_score,
            r.stddev_fantasy_score,
            r.stats_source
           FROM rolling_stats r
          WHERE NOT (EXISTS ( SELECT 1
                   FROM current_season_stats c
                  WHERE c.player_id = r.player_id))
        ), historical_seasons AS (
         SELECT player_season_fantasy_stats.player_id,
            player_season_fantasy_stats.season_id AS current_season_id,
            player_season_fantasy_stats.avg_fantasy_score,
            player_season_fantasy_stats.stddev_fantasy_score,
            'season'::text AS stats_source
           FROM player_season_fantasy_stats
          WHERE player_season_fantasy_stats.season_id::text <> (( SELECT current_season_config.season_id
                   FROM current_season_config
                  WHERE current_season_config.id = 1)) AND player_season_fantasy_stats.games_played >= 20
        ), all_seasons AS (
         SELECT historical_seasons.player_id,
            historical_seasons.current_season_id,
            historical_seasons.avg_fantasy_score,
            historical_seasons.stddev_fantasy_score,
            historical_seasons.stats_source
           FROM historical_seasons
        UNION ALL
         SELECT current_season_bootstrapped.player_id,
            current_season_bootstrapped.current_season_id,
            current_season_bootstrapped.avg_fantasy_score,
            current_season_bootstrapped.stddev_fantasy_score,
            current_season_bootstrapped.stats_source
           FROM current_season_bootstrapped
        ), adjusted AS (
         SELECT a.player_id,
            a.current_season_id,
            a.avg_fantasy_score AS raw_avg,
            a.stddev_fantasy_score AS raw_stddev,
            a.stats_source,
            CASE WHEN a.stats_source = 'rolling' AND m.player_id IS NOT NULL THEN 0.90 ELSE 1.0 END AS factor
           FROM all_seasons a
           LEFT JOIN nba_player_movers m ON m.player_id = a.player_id
        )
 SELECT player_id,
    current_season_id AS season_id,
    round(raw_avg * factor, 2) AS avg_fantasy_score,
    round(raw_stddev * factor, 2) AS stddev_fantasy_score,
    stats_source,
    round(raw_avg * factor + 1.25 * raw_stddev * factor, 2) AS eligibility_ceiling
   FROM adjusted
  WHERE (raw_avg + 1.25 * raw_stddev) >= 35::numeric;
