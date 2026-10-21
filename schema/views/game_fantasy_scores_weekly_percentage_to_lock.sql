-- game_fantasy_scores_weekly_percentage_to_lock: same columns as before
-- (copied from the live \sv output 10/21/26); the ONLY change is that tiers come
-- from nba_player_tiers_in_use instead of player_tiers, so the current season
-- borrows last season's tiers until it has enough ranked players.
-- Safe to run: CREATE OR REPLACE with identical column names/types, so
-- game_lock_signal and the other dependents are untouched (no DROP, no CASCADE).
-- Run nba_player_tiers_in_use.sql first.
CREATE OR REPLACE VIEW public.game_fantasy_scores_weekly_percentage_to_lock AS
 SELECT gfsw.game_id,
    gfsw.player_id,
    gfsw.team_id,
    gfsw.opponent_team_id,
    gfsw.season_id,
    gfsw.game_date,
    gfsw.is_home,
    gfsw.wl,
    gfsw.minutes,
    gfsw.pts,
    gfsw.reb,
    gfsw.oreb,
    gfsw.dreb,
    gfsw.ast,
    gfsw.stl,
    gfsw.blk,
    gfsw.tov,
    gfsw.fgm,
    gfsw.fga,
    gfsw.ftm,
    gfsw.fta,
    gfsw.fg3m,
    gfsw.fg3a,
    gfsw.plus_minus,
    gfsw.double_digit_categories,
    gfsw.is_double_double,
    gfsw.is_triple_double,
    gfsw.fantasy_score,
    gfsw.week_number,
    gfsw.week_start_date,
    gfsw.week_end_date,
    gfsw.is_playoff_week,
    gfsw.games_before_in_week,
    gfsw.games_remaining_in_week,
    gfsw.total_team_games_this_week,
    gfsw.is_last_game_of_week,
    gfsw.effective_games_remaining_in_week,
    pt.tier,
    round(1::numeric - hold_win_probability_by_tier(gfsw.effective_games_remaining_in_week, pt.tier), 4) AS percentage_to_lock
   FROM game_fantasy_scores_weekly_effective gfsw
     JOIN nba_player_tiers_in_use pt ON pt.player_id = gfsw.player_id AND pt.season_id::text = gfsw.season_id::text;
