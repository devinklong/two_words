-- Players who changed teams since last season: their team in the last game before this season
-- (cutoff = Sept 1 of the current season's start year) differs from their current NBA team.
-- Needs nba_player_current_team (refreshed by run_daily). If that snapshot is empty, this view is empty.
CREATE OR REPLACE VIEW nba_player_movers AS
WITH cutoff AS (
    SELECT make_date(substr(season_id, 2)::int, 9, 1) AS d
    FROM current_season_config
    WHERE id = 1
), last_prev AS (
    SELECT DISTINCT ON (g.player_id) g.player_id, g.team_id AS old_team
    FROM game_logs g, cutoff
    WHERE g.game_date < cutoff.d
    ORDER BY g.player_id, g.game_date DESC
)
SELECT l.player_id, l.old_team, c.team_id AS new_team
FROM last_prev l
JOIN nba_player_current_team c ON c.player_id = l.player_id AND c.team_id <> l.old_team;
