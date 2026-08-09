-- team_schedule: one row per team per game (get_team_schedule() pulls one
-- team's perspective per call, looped over all 30 teams — so every game
-- produces two rows, one per team, sharing the same game_id).
--
-- PK is (game_id, team_id), not game_id alone. Originally this table
-- shipped with a solo game_id PK, which collided on the second row of
-- every game — load_team_schedule.py silently lost half its data to
-- ON CONFLICT DO NOTHING as a result. That was caught and corrected via
-- fix_team_schedule_pk.sql (8/26). The composite PK is baked directly into
-- this CREATE TABLE now, so a fresh run of this file alone is correct —
-- fix_team_schedule_pk.sql is kept only as a historical record of the
-- original bug and isn't required for new setups.

DROP TABLE IF EXISTS team_schedule;

CREATE TABLE team_schedule (
    game_id          VARCHAR(20) NOT NULL,
    season_id        VARCHAR(10),
    team_id          INTEGER REFERENCES teams(team_id),
    opponent_team_id INTEGER REFERENCES teams(team_id),
    game_date        DATE NOT NULL,
    is_home          BOOLEAN,
    wl               CHAR(1),
    pts              INTEGER,
    plus_minus       NUMERIC,
    -- fgm, fga, ftm, fta, oreb, dreb, ast, stl, blk, tov, pf, etc.
    PRIMARY KEY (game_id, team_id)
);

-- =========================
-- Verification
-- =========================

-- Confirm the composite PK actually took
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'team_schedule'::regclass AND contype = 'p';

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22024';

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22023';

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22022';

SELECT COUNT(*) FROM team_schedule WHERE season_id = '22021';

-- Every game_id should appear exactly twice (one row per team)
SELECT game_id, COUNT(*) FROM team_schedule GROUP BY game_id HAVING COUNT(*) != 2;
