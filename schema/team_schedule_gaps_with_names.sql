-- Same top-gap-count query as before, but joined to players/teams for names,
-- plus how many team_schedule games fell inside each player's tenure window
-- and how many of those they actually logged. A player with e.g. 77 gaps
-- out of only 82 possible games in their window (5 logged) is very plausibly
-- a two-way/G-League-assignment player who barely played — not a data bug.
-- A player with 77 gaps out of, say, 78 games in a *narrow* window would be
-- more suspicious and worth digging into.

SELECT
    p.full_name,
    t.full_name AS team_name,
    g.gap_count,
    w.games_in_window,
    (w.games_in_window - g.gap_count) AS games_logged
FROM (
    SELECT player_id, team_id, COUNT(*) AS gap_count
    FROM team_schedule_gaps
    GROUP BY player_id, team_id
) g
JOIN players p ON p.player_id = g.player_id
JOIN teams t ON t.team_id = g.team_id
JOIN (
    SELECT
        gl.player_id,
        gl.team_id,
        COUNT(*) AS games_in_window
    FROM team_schedule ts
    JOIN (
        SELECT player_id, team_id, MIN(game_date) AS first_game, MAX(game_date) AS last_game
        FROM game_logs
        GROUP BY player_id, team_id
    ) win ON win.team_id = ts.team_id
        AND ts.game_date BETWEEN win.first_game AND win.last_game
    JOIN (SELECT DISTINCT player_id, team_id FROM game_logs) gl
        ON gl.player_id = win.player_id AND gl.team_id = win.team_id
    GROUP BY gl.player_id, gl.team_id
) w ON w.player_id = g.player_id AND w.team_id = g.team_id
ORDER BY g.gap_count DESC
LIMIT 20;
