-- Detects games where a player's team played (per team_schedule) but the
-- player has no corresponding row in game_logs — i.e. a DNP, coach's
-- decision, injury, or any other silent absence nba_api's game log endpoint
-- doesn't surface on its own.
--
-- Design note: players has no team_id (team affiliation is time-varying,
-- deliberately not stored there), so a player's team must be inferred from
-- game_logs itself. player_team_windows below derives, per player+team+
-- SEASON, the first and last date they actually logged a game for that
-- team — the JOIN then only checks team_schedule games falling inside that
-- window, for that same season.
--
-- Season scoping is required, not optional, once more than one season is
-- loaded: without it, a player who left a team and later rejoined it (a
-- multi-year stint gap, not unusual across 5 seasons of data) would get a
-- window spanning from their very first game to their very last across
-- every stint — falsely flagging every game the team played in the seasons
-- between as a "gap," even though the player wasn't on the roster then at
-- all. Bounding by season_id keeps each season's window independent.
--
-- Known limitation: still can't catch a DNP on the exact game a player
-- joined or left a team within a season (the window is defined by games
-- they DID log), so a coach's-decision DNP on a player's first day with a
-- new team would be invisible to this — same edge case as before, just
-- documented here again since it hasn't changed.

DROP VIEW IF EXISTS team_schedule_gaps;

CREATE VIEW team_schedule_gaps AS
WITH player_team_windows AS (
    SELECT
        player_id,
        team_id,
        season_id,
        MIN(game_date) AS first_game,
        MAX(game_date) AS last_game
    FROM game_logs
    GROUP BY player_id, team_id, season_id
)
SELECT
    ptw.player_id,
    ts.team_id,
    ts.game_id,
    ts.game_date,
    ts.opponent_team_id,
    ts.is_home,
    ts.season_id
FROM team_schedule ts
JOIN player_team_windows ptw
    ON ptw.team_id = ts.team_id
    AND ptw.season_id = ts.season_id
    AND ts.game_date BETWEEN ptw.first_game AND ptw.last_game
LEFT JOIN game_logs gl
    ON gl.game_id = ts.game_id
    AND gl.player_id = ptw.player_id
WHERE gl.game_id IS NULL
ORDER BY ptw.player_id, ts.game_date;

-- =========================
-- Verification
-- =========================

-- Total gaps found across all players
SELECT COUNT(*) AS total_gaps FROM team_schedule_gaps;

-- Sanity check against your earlier pandas validation — Raynaud/Sacramento
-- found 8 confirmed gaps. player_id 1642875 is Raynaud, team_id 1610612758
-- is Sacramento (per your verify_gamelog_columns.py output).
SELECT * FROM team_schedule_gaps
WHERE player_id = 1642875
ORDER BY game_date;

-- Which players have the most gaps overall — useful eyeball check for
-- anything suspiciously high (might indicate a trade the window logic
-- didn't handle cleanly, rather than genuine DNPs)
SELECT player_id, team_id, COUNT(*) AS gap_count
FROM team_schedule_gaps
GROUP BY player_id, team_id
ORDER BY gap_count DESC
LIMIT 20;

SELECT COUNT(*) FROM team_schedule_gaps;

SELECT player_id, game_id, COUNT(*) 
FROM team_schedule_gaps 
GROUP BY player_id, game_id 
HAVING COUNT(*) > 1;

SELECT * FROM players WHERE player_id = 203648;
