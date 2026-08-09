-- HISTORICAL CONTEXT (8/2/26): game_logs.player_id was always supposed to
-- have a FK to players, but the live table never actually got the
-- constraint applied — 15 players' game log rows got in without a
-- matching players row, since nba_api's static player list hadn't caught
-- up to some recent additions. Fixed by backfilling the missing players
-- (backfill_missing_players.py) then adding the constraint here.
--
-- game_logs.sql's CREATE TABLE now includes the player_id REFERENCES
-- inline, so a FRESH table already has this constraint (Postgres
-- auto-names it game_logs_player_id_fkey, same name used below). This
-- file is now idempotent — it checks whether the constraint already
-- exists before adding it, so it's safe to run either on a fresh table
-- (no-op) or an older table that predates the inline fix (applies it).
--
-- If you're setting up fresh, running this file at all is optional —
-- game_logs.sql already covers it. Kept as a historical record and a
-- safety net for existing databases.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'game_logs_player_id_fkey'
          AND conrelid = 'game_logs'::regclass
    ) THEN
        ALTER TABLE game_logs
        ADD CONSTRAINT game_logs_player_id_fkey
        FOREIGN KEY (player_id) REFERENCES players(player_id);
    END IF;
END $$;

-- =========================
-- Verification
-- =========================

-- Verify all three FKs now exist
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'game_logs'::regclass AND contype = 'f';
