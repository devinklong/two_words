-- nba_lock_decision_outcomes: every lock in nba_lock_decisions (all teams) with what happened to that player afterward.
-- For each lock: the games the same player played LATER that fantasy week (the ones the lock gave up on),
-- the best of them, and whether the lock turned out right. Safe to re-run (CREATE OR REPLACE).
--   later_games         games he played after the locked game that week (0 = nothing left to give up)
--   best_later_score    his best score in those games
--   lock_was_right      locked score >= every later score (TRUE also when there were no later games)
--   points_given_up     how much better the best later game was (0 if the lock was right)
--   followed_model      the model said LOCK when you locked (bar/signal are the values frozen at lock time)
--   week_closed         the fantasy week is over, so later_games is complete (open weeks can still grow)
-- Example: how often do locks at/above the bar hold up, by team?
--   SELECT roster_id, COUNT(*), ROUND(AVG((lock_was_right)::int),3) FROM nba_lock_decision_outcomes
--   WHERE week_closed AND followed_model GROUP BY 1 ORDER BY 3 DESC;
CREATE OR REPLACE VIEW nba_lock_decision_outcomes AS
SELECT d.league_id, d.roster_id, d.season_id, d.week_number, d.slot,
       d.player_name, d.nba_player_id, d.game_date AS locked_game_date,
       d.fantasy_score AS locked_score, d.lock_bar, d.fantasy_score - d.lock_bar AS gap_over_bar,
       d.model_signal, d.model_percentage_to_lock, d.tier, d.stats_source, d.games_left_effective,
       (d.model_signal = 'LOCK') AS followed_model,
       d.source, d.recorded_at,
       COALESCE(l.later_games, 0)          AS later_games,
       l.best_later_score,
       (l.best_later_score IS NULL OR d.fantasy_score >= l.best_later_score) AS lock_was_right,
       GREATEST(COALESCE(l.best_later_score, 0) - d.fantasy_score, 0)        AS points_given_up,
       (w.week_end_date < CURRENT_DATE)    AS week_closed
FROM nba_lock_decisions d
LEFT JOIN fantasy_weeks w ON w.season_id = d.season_id AND w.week_number = d.week_number
LEFT JOIN LATERAL (
    SELECT COUNT(*) AS later_games, MAX(e.fantasy_score) AS best_later_score
    FROM game_fantasy_scores_weekly_effective e
    WHERE e.player_id = d.nba_player_id AND e.season_id = d.season_id
      AND e.week_number = d.week_number AND e.game_date > d.game_date
) l ON TRUE;
