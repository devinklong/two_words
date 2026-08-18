-- ONE-TIME migration: converts game_fantasy_scores_weekly_effective from a
-- MATERIALIZED VIEW (full drop-and-recompute of ALL ~122k+ rows on every
-- rebuild -- increasingly wasteful as the season grows, since only a
-- handful of rows are ever actually new) into a real TABLE, kept current
-- by sync_game_fantasy_scores_weekly_effective.sql's incremental catch-up
-- instead. Downstream objects (game_fantasy_scores_weekly_percentage_to_lock,
-- game_lock_signal, etc.) all just SELECT FROM this name -- SQL can't
-- tell a table from a materialized view, so nothing downstream needs to
-- change. Run this ONCE.
--
-- AFTER THIS RUNS: rebuild_materialized_views.sql's Step 2 (this object)
-- is obsolete -- do NOT run it again, it would recreate the materialized
-- view and silently undo this migration. Step 1 (team_schedule_b2b_flags)
-- is UNCHANGED and still valid -- that one only needs rebuilding if the
-- schedule itself changes (postponement, makeup game), not nightly.
--
-- REQUIRED IMMEDIATELY AFTER THIS FILE: the CASCADE below takes
-- game_fantasy_scores_weekly_percentage_to_lock and game_lock_signal down with
-- it. Rerun percentage_to_lock.sql then game_lock_signal.sql to rebuild
-- both before trusting the schema again.

DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE relname = 'game_fantasy_scores_weekly_effective';
    IF kind IS NULL THEN
        RAISE EXCEPTION 'game_fantasy_scores_weekly_effective does not exist -- run rebuild_materialized_views.sql first to create it, then this migration.';
    ELSIF kind != 'm' THEN
        RAISE EXCEPTION 'Expected a MATERIALIZED VIEW (relkind=m), found relkind=%. Already migrated? Check by hand before proceeding.', kind;
    END IF;
END $$;

-- CASCADE (needed -- confirmed 8/10/26): game_fantasy_scores_weekly_lock_
-- signal and, transitively, game_lock_signal both depend on this object.
-- Without CASCADE the drop fails outright -- same failure mode already
-- fixed once tonight in percentage_to_lock.sql, reappearing here since
-- this is a different file. CASCADE takes both dependents down with it --
-- REQUIRED AFTER THIS MIGRATION: rerun percentage_to_lock.sql then
-- game_lock_signal.sql to rebuild them (same two-file sequence as any
-- other percentage_to_lock.sql redeploy).

DROP TABLE IF EXISTS game_fantasy_scores_weekly_effective_new;

CREATE TABLE game_fantasy_scores_weekly_effective_new AS
SELECT * FROM game_fantasy_scores_weekly_effective;

DROP MATERIALIZED VIEW game_fantasy_scores_weekly_effective CASCADE;

ALTER TABLE game_fantasy_scores_weekly_effective_new RENAME TO game_fantasy_scores_weekly_effective;

-- Composite PK matches game_logs' own (player_id, game_id) convention,
-- and is what sync_game_fantasy_scores_weekly_effective.sql's anti-join
-- uses to detect "already present" rows.
ALTER TABLE game_fantasy_scores_weekly_effective
    ADD PRIMARY KEY (player_id, game_id);

CREATE INDEX idx_gfswe_player_season_week_date
ON game_fantasy_scores_weekly_effective (player_id, season_id, week_number, game_date);

ANALYZE game_fantasy_scores_weekly_effective;

-- =========================
-- Verification
-- =========================

SELECT relname, relkind,
       CASE relkind WHEN 'r' THEN 'table (correct)'
                     WHEN 'm' THEN 'STILL A MATERIALIZED VIEW -- migration failed'
                     ELSE 'unexpected type' END AS status
FROM pg_class
WHERE relname = 'game_fantasy_scores_weekly_effective';

SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'game_fantasy_scores_weekly_effective'::regclass AND contype = 'p';

SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective;  -- should match the old materialized view's row count exactly
