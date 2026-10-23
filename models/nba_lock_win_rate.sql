-- nba_lock_win_rate: how often locking a score AT OR ABOVE the player's lock bar turned out to be right,
-- from history. "Right" = no later game in the same fantasy week beat that score (a later game he did not
-- play counts as not beating it). Split by tier, games left in the week (1-4) and how far above the bar
-- the score was (bucket a 0-5, b 5-10, c 10-15, d 15+ points). Added 10/23/26.
--
-- Why this exists: hold_win_probability_by_tier / percentage_to_lock is fitted ONLY on games BELOW the bar
-- (see scripts/fit_hold_value_curve_by_tier.py), so it is the right odds for HOLD rows and the wrong ones
-- for LOCK rows (a LOCK showed ~43% with one game left; the history says 83-98%).
-- Same population rules as the fit script: player_season_fantasy_stats with 20+ games and spike >= 35,
-- tier by rank in season (25 / 75), lock_bar(avg, sd, 35, 0.5). Games with few cases (k=4) are sparse:
-- consumers should cap k at 3.
--
-- Depends on: player_season_fantasy_stats, game_fantasy_scores_weekly_effective, lock_bar().
-- scripts/rebuild_lock_pipeline.py recreates it after game_lock_signal.
CREATE OR REPLACE VIEW nba_lock_win_rate AS
WITH ranked_pool AS (
    SELECT player_id, season_id, avg_fantasy_score, stddev_fantasy_score,
           ROW_NUMBER() OVER (PARTITION BY season_id ORDER BY avg_fantasy_score DESC, player_id) AS rank_in_season
    FROM player_season_fantasy_stats
    WHERE games_played >= 20 AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35
), tiered_pool AS (
    SELECT *,
           CASE WHEN rank_in_season <= 25 THEN '1_elite' WHEN rank_in_season <= 75 THEN '2_mid' ELSE '3_lower' END AS tier,
           lock_bar(avg_fantasy_score, stddev_fantasy_score, 35, 0.5) AS lock_bar
    FROM ranked_pool
), full_week_games AS (
    SELECT g.player_id, g.season_id, g.week_number, g.game_date, g.games_remaining_in_week,
           g.fantasy_score, tp.tier, tp.lock_bar
    FROM game_fantasy_scores_weekly_effective g
    JOIN tiered_pool tp ON tp.player_id = g.player_id AND tp.season_id = g.season_id
), with_future AS (
    SELECT *,
           MAX(fantasy_score) OVER (PARTITION BY player_id, season_id, week_number ORDER BY game_date DESC
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS best_remaining_score
    FROM full_week_games
)
SELECT tier,
       games_remaining_in_week AS games_left,
       CASE WHEN fantasy_score - lock_bar < 5 THEN 'a 0-5'
            WHEN fantasy_score - lock_bar < 10 THEN 'b 5-10'
            WHEN fantasy_score - lock_bar < 15 THEN 'c 10-15'
            ELSE 'd 15+' END AS above_bar,
       COUNT(*) AS n,
       ROUND(1 - AVG((COALESCE(best_remaining_score, -1) > fantasy_score)::int), 3) AS lock_was_right
FROM with_future
WHERE fantasy_score >= lock_bar AND games_remaining_in_week BETWEEN 1 AND 4
GROUP BY 1, 2, 3;
