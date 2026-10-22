-- nba_player_current_team: each player's NBA team right now, a snapshot refreshed by
-- scripts/ingestion/load_current_teams.py (nba_api CommonAllPlayers). Added 10/21/26.
-- team_id 0 / roster_status 0 = not on an NBA roster (retired, unsigned, overseas).
-- The waiver tools use it to show which free agents play on which days, because
-- game_logs only knows the team a player LAST PLAYED for (stale after a trade or signing).
-- No FK to nba_players on purpose: a new player can appear here before nba_players has him.
-- Additive and idempotent.
CREATE TABLE IF NOT EXISTS nba_player_current_team (
    player_id      INTEGER PRIMARY KEY,
    team_id        INTEGER NOT NULL,
    roster_status  INTEGER,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
