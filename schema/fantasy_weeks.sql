-- Maps each season to its 27 fantasy weeks (24 regular season + 3 playoff),
-- Monday-Sunday, starting from the Monday of the week containing that
-- season's actual first game (per team_schedule). Playoff byes for the top
-- 2 seeds affect WHICH TEAMS play in week 25, not the date boundaries
-- themselves, so this table doesn't need to know about standings/byes at
-- all — it's purely a date-to-week lookup.

DROP TABLE IF EXISTS fantasy_weeks;

CREATE TABLE fantasy_weeks (
    season_id       VARCHAR(10) NOT NULL,
    week_number     INTEGER     NOT NULL,
    week_start_date DATE        NOT NULL,
    week_end_date   DATE        NOT NULL,
    is_playoff_week BOOLEAN     NOT NULL,
    PRIMARY KEY (season_id, week_number)
);

WITH season_starts AS (
    SELECT
        season_id,
        MIN(game_date) AS first_game_date
    FROM team_schedule
    GROUP BY season_id
),
week1_monday AS (
    SELECT
        season_id,
        -- ISODOW: Monday=1 ... Sunday=7. Subtracting (isodow-1) days walks
        -- back to the Monday of the week containing the first game.
        first_game_date - (EXTRACT(ISODOW FROM first_game_date)::INT - 1) AS week1_start
    FROM season_starts
)
INSERT INTO fantasy_weeks (season_id, week_number, week_start_date, week_end_date, is_playoff_week)
SELECT
    w.season_id,
    n AS week_number,
    w.week1_start + (n - 1) * 7 AS week_start_date,
    w.week1_start + (n - 1) * 7 + 6 AS week_end_date,
    (n > 24) AS is_playoff_week
FROM week1_monday w
CROSS JOIN generate_series(1, 27) AS n
ORDER BY w.season_id, n;

-- =========================
-- Verification
-- =========================

-- Should be exactly 5 seasons x 27 weeks = 135 rows
SELECT COUNT(*) FROM fantasy_weeks;

-- 24 regular + 3 playoff per season, every season
SELECT season_id, is_playoff_week, COUNT(*)
FROM fantasy_weeks
GROUP BY season_id, is_playoff_week
ORDER BY season_id, is_playoff_week;

-- Eyeball one season's full week list — confirm Monday starts, Sunday
-- ends, and no gaps/overlaps between consecutive weeks
SELECT * FROM fantasy_weeks
WHERE season_id = '22024'
ORDER BY week_number;

-- Confirm week1_start actually falls on or before that season's real
-- first game date, and is genuinely a Monday
SELECT
    fw.season_id,
    fw.week_start_date,
    EXTRACT(ISODOW FROM fw.week_start_date) AS should_be_1_for_monday,
    ts.first_game_date
FROM fantasy_weeks fw
JOIN (
    SELECT season_id, MIN(game_date) AS first_game_date
    FROM team_schedule
    GROUP BY season_id
) ts ON ts.season_id = fw.season_id
WHERE fw.week_number = 1;
