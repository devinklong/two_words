-- Consolidates materialize_b2b_flags.sql + materialize_game_fantasy_scores_
-- weekly_effective.sql into ONE script, run in the correct dependency
-- order, using type-safe drops.
--
-- ROOT CAUSE this fixes (8/8/26): two separate schema files both defined
-- an object named game_fantasy_scores_weekly_effective — one as a plain
-- VIEW (game_fantasy_scores_weekly_effective_view.sql), one as a
-- MATERIALIZED VIEW (materialize_game_fantasy_scores_weekly_effective.sql).
-- `DROP VIEW IF EXISTS x` errors out (not silently) if x is currently a
-- MATERIALIZED VIEW, and `DROP MATERIALIZED VIEW IF EXISTS x` errors out
-- if x is currently a plain VIEW — IF EXISTS only covers "doesn't exist",
-- not "wrong object type." Combined with materialize_b2b_flags.sql's
-- CASCADE drop taking game_fantasy_scores_weekly_effective down with it
-- (documented in that file's own comment), it was easy for the two
-- materialize scripts to fall out of sync or for the old plain-view file
-- to get rerun by mistake — silently reverting the materialization and
-- bringing back the exact 122,569-call slowdown that started this.
--
-- Fix: a DO block checks pg_class.relkind before dropping, so this script
-- works correctly no matter what the object currently is (or if it
-- doesn't exist at all yet). Delete/archive the old
-- game_fantasy_scores_weekly_effective_view.sql and
-- materialize_b2b_flags.sql / materialize_game_fantasy_scores_weekly_
-- effective.sql from your schema/ folder once this replaces them, so
-- there's exactly ONE file that can create this object going forward.
--
-- Run this whenever team_schedule or game_logs changes (season backfill,
-- data correction) — it fully rebuilds both materialized views in the
-- right order every time.

-- =========================
-- Step 1: team_schedule_b2b_flags (base dependency — must rebuild first)
-- =========================

DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE relname = 'team_schedule_b2b_flags';
    IF kind = 'm' THEN
        EXECUTE 'DROP MATERIALIZED VIEW team_schedule_b2b_flags CASCADE';
    ELSIF kind = 'v' THEN
        EXECUTE 'DROP VIEW team_schedule_b2b_flags CASCADE';
    END IF;
    -- kind IS NULL means it doesn't exist yet — nothing to drop
END $$;

CREATE MATERIALIZED VIEW team_schedule_b2b_flags AS
SELECT
    ts.*,
    (ts.game_date - LAG(ts.game_date) OVER (
        PARTITION BY ts.team_id, ts.season_id ORDER BY ts.game_date
    )) = 1 AS is_second_night_of_b2b,
    (LEAD(ts.game_date) OVER (
        PARTITION BY ts.team_id, ts.season_id ORDER BY ts.game_date
    ) - ts.game_date) = 1 AS is_first_night_of_b2b
FROM team_schedule ts;

CREATE INDEX idx_b2b_flags_team_season_date
ON team_schedule_b2b_flags (team_id, season_id, game_date);

ANALYZE team_schedule_b2b_flags;

-- =========================
-- Step 2: game_fantasy_scores_weekly_effective (depends on step 1)
-- =========================
-- The CASCADE from step 1's drop will already have removed this if it
-- existed, but this type-safe drop is here too in case step 1 was
-- skipped or this object was rebuilt independently since.

DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE relname = 'game_fantasy_scores_weekly_effective';
    IF kind = 'm' THEN
        EXECUTE 'DROP MATERIALIZED VIEW game_fantasy_scores_weekly_effective CASCADE';
    ELSIF kind = 'v' THEN
        EXECUTE 'DROP VIEW game_fantasy_scores_weekly_effective CASCADE';
    END IF;
END $$;

CREATE MATERIALIZED VIEW game_fantasy_scores_weekly_effective AS
SELECT
    gfsw.*,
    (
        SELECT COALESCE(ROUND(SUM(
            CASE WHEN b2b.is_second_night_of_b2b THEN 0.9805 ELSE 1.0 END
        ), 3), 0)
        FROM team_schedule_b2b_flags b2b
        WHERE b2b.team_id = gfsw.team_id
          AND b2b.season_id = gfsw.season_id
          AND b2b.game_date > gfsw.game_date
          AND b2b.game_date BETWEEN gfsw.week_start_date AND gfsw.week_end_date
    ) AS effective_games_remaining_in_week
FROM game_fantasy_scores_weekly_full gfsw;

CREATE INDEX idx_gfswe_player_season_week_date
ON game_fantasy_scores_weekly_effective (player_id, season_id, week_number, game_date);

ANALYZE game_fantasy_scores_weekly_effective;

-- =========================
-- Verification
-- =========================

-- Confirm both objects are actually materialized views now, not plain views
SELECT relname, relkind,
       CASE relkind WHEN 'm' THEN 'materialized view (correct)'
                     WHEN 'v' THEN 'PLAIN VIEW — rebuild failed'
                     ELSE 'unexpected type' END AS status
FROM pg_class
WHERE relname IN ('team_schedule_b2b_flags', 'game_fantasy_scores_weekly_effective');

SELECT COUNT(*) FROM team_schedule_b2b_flags;
SELECT COUNT(*) FROM team_schedule;  -- should match exactly

SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective;

SELECT COUNT(*) AS violations
FROM game_fantasy_scores_weekly_effective
WHERE effective_games_remaining_in_week > games_remaining_in_week;

-- Reconfirm the Jokić spot check still matches
SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.effective_games_remaining_in_week, gfsw.is_last_game_of_week
FROM game_fantasy_scores_weekly_effective gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
