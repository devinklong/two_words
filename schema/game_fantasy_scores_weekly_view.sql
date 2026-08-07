-- Joins fantasy_weeks onto game_fantasy_scores so every game carries its
-- season-scoped fantasy week_number, week_start_date/end_date, and whether
-- it falls in a playoff week. Join key: same season_id, and game_date
-- falling inside that week's Monday-Sunday range.

DROP VIEW IF EXISTS game_fantasy_scores_weekly;

CREATE VIEW game_fantasy_scores_weekly AS
SELECT
    gfs.*,
    fw.week_number,
    fw.week_start_date,
    fw.week_end_date,
    fw.is_playoff_week
FROM game_fantasy_scores gfs
JOIN fantasy_weeks fw
    ON fw.season_id = gfs.season_id
    AND gfs.game_date BETWEEN fw.week_start_date AND fw.week_end_date;

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM game_fantasy_scores_weekly;
SELECT COUNT(*) FROM game_fantasy_scores;

-- Orphan check: any game whose date doesn't fall inside ANY of its
-- season's 24 weeks. Should return 0 rows — if not, the fantasy_weeks
-- date range doesn't fully cover that season's actual game dates.
SELECT gfs.game_id, gfs.player_id, gfs.season_id, gfs.game_date
FROM game_fantasy_scores gfs
LEFT JOIN fantasy_weeks fw
    ON fw.season_id = gfs.season_id
    AND gfs.game_date BETWEEN fw.week_start_date AND fw.week_end_date
WHERE fw.season_id IS NULL;

-- Games-per-week distribution, one season — sanity check that weeks have
-- a plausible game count (should be nonzero for regular season weeks;
-- worth specifically checking whether the 3 "playoff" weeks (22-24) show
-- real NBA game counts or drop to zero — team_schedule only contains
-- regular-season games, so if the fantasy playoff weeks extend past the
-- actual NBA regular season's end date, those weeks would show 0 games,
-- which would be a real problem for the model, not just a labeling quirk)
SELECT week_number, is_playoff_week, COUNT(*) AS game_count
FROM game_fantasy_scores_weekly
WHERE season_id = '22024'
GROUP BY week_number, is_playoff_week
ORDER BY week_number;

-- Spot check one player's specific week — pick a known player and week,
-- confirm the games listed match reality by cross-referencing team_schedule
SELECT p.full_name, gfsw.game_date, gfsw.week_number, gfsw.fantasy_score
FROM game_fantasy_scores_weekly gfsw
JOIN players p ON p.player_id = gfsw.player_id
WHERE p.full_name ILIKE '%joki%' AND gfsw.season_id = '22024' AND gfsw.week_number = 5
ORDER BY gfsw.game_date;
