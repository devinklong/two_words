-- Rebuilds the team_schedule_b2b_flags materialized view. No CASCADE: nothing is built on it (live check 11/8/26), so a new
-- dependent makes the drop fail loudly. Rerun whenever team_schedule or game_logs changes. Uses a
-- type-safe drop (checks pg_class.relkind first) since a plain
-- DROP VIEW/DROP MATERIALIZED VIEW errors out -- not silently -- if the
-- object currently exists as the OTHER type, which happened once when two
-- schema files both defined this object differently.

-- =========================
-- Step 1: team_schedule_b2b_flags (base dependency)
-- =========================

DO $$
DECLARE
    kind "char";
BEGIN
    SELECT relkind INTO kind FROM pg_class WHERE relname = 'team_schedule_b2b_flags';
    IF kind = 'm' THEN
        EXECUTE 'DROP MATERIALIZED VIEW team_schedule_b2b_flags';
    ELSIF kind = 'v' THEN
        EXECUTE 'DROP VIEW team_schedule_b2b_flags';
    END IF;
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

-- Step 2 (game_fantasy_scores_weekly_effective) was REMOVED 11/8/26. That object is a real table now, kept current by
-- schema/tables/sync_game_fantasy_scores_weekly_effective.sql; the old code here would have recreated it as a materialized view.

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM team_schedule_b2b_flags;
SELECT COUNT(*) FROM team_schedule;  -- should match exactly
