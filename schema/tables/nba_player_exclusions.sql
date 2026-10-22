-- nba_player_exclusions: players the waiver tools should never show, even though they
-- are unrostered in the league (retired, overseas, out for the season, not worth tracking).
-- Added 10/21/26. Neither the NBA nor Sleeper APIs distinguish a retired player from an
-- unsigned free agent, so this list is maintained by hand:
--   INSERT INTO nba_player_exclusions (nba_player_id, reason)
--   SELECT player_id, 'retired' FROM nba_players WHERE full_name = 'Russell Westbrook'
--   ON CONFLICT DO NOTHING;
-- Remove a row to bring a player back. Read by waiver_wire_finder.get_free_agents(),
-- so waiver_bid_advisor.py inherits it. Additive and idempotent.
CREATE TABLE IF NOT EXISTS nba_player_exclusions (
    nba_player_id  INTEGER PRIMARY KEY REFERENCES nba_players(player_id),
    reason         TEXT,
    added_on       DATE NOT NULL DEFAULT CURRENT_DATE
);
