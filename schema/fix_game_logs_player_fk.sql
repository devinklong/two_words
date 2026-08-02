-- game_logs.player_id was always supposed to have a FK to players (per the
-- original DDL text), but the live table never actually got the constraint
-- applied — confirmed via pg_constraint, which only shows team_id and
-- opponent_team_id FKs. This let 15 players' game log rows in without a
-- matching players row, since nba_api's static player list (used by
-- load_players.py) hadn't caught up to some recent additions.
--
-- Run backfill_missing_players.py FIRST to add the missing players, or this
-- ALTER will fail — you can't add a FK constraint while orphaned rows exist.

ALTER TABLE game_logs
ADD CONSTRAINT game_logs_player_id_fkey
FOREIGN KEY (player_id) REFERENCES players(player_id);

-- Verify all three FKs now exist
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'game_logs'::regclass AND contype = 'f';
