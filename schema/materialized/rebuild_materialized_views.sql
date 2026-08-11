-- Rebuilds both materialized views (team_schedule_b2b_flags, then
-- game_fantasy_scores_weekly_effective, which depends on it) in the correct
-- order. Rerun whenever team_schedule or game_logs changes. Uses a
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
        EXECUTE 'DROP MATERIALIZED VIEW team_schedule_b2b_flags CASCADE';
    ELSIF kind = 'v' THEN
        EXECUTE 'DROP VIEW team_schedule_b2b_flags CASCADE';
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

-- =========================
-- Step 2: game_fantasy_scores_weekly_effective (depends on step 1)
-- =========================
-- RETIRED (8/10/26) -- DO NOT RUN THIS STEP. This object is now a real
-- TABLE (migrate_game_fantasy_scores_weekly_effective_to_table.sql),
-- kept current via sync_game_fantasy_scores_weekly_effective.sql instead
-- of a full rebuild. Running the code below would DROP the table and
-- recreate it as a materialized view again, silently undoing the
-- migration. Left as a historical record -- Step 1 above is still valid.
-- Step 1's CASCADE already dropped this if it existed; type-safe drop
-- here too in case this object was rebuilt independently since.

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
        -- 0.9805 = measured B2B second-night efficiency multiplier; a
        -- future B2B game counts as slightly less than one full "chance"
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

SELECT p.full_name, gfsw.game_date, gfsw.games_remaining_in_week,
       gfsw.effective_games_remaining_in_week, gfsw.is_last_game_of_week
FROM game_fantasy_scores_weekly_effective gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
