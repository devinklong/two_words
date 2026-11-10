-- No DROP ... CASCADE: CREATE OR REPLACE keeps every view built on this one in place, so re-running this file is safe. If a column ever has to
-- change name, type or position, Postgres refuses with an error; drop the dependents by hand in that case (never with CASCADE).

-- fantasy_matchups: pairs each roster with its opponent for a given
-- week, self-joining sleeper_matchups on (league_id, week, matchup_id).
-- LEFT JOIN, not INNER -- a bye week (odd number of teams, or a
-- matchup_id with no counterpart row) still produces a row with
-- opponent_roster_id = NULL instead of disappearing.

CREATE OR REPLACE VIEW fantasy_matchups AS
SELECT
    m1.league_id,
    m1.week,
    m1.matchup_id,
    m1.roster_id,
    m2.roster_id AS opponent_roster_id
FROM sleeper_matchups m1
LEFT JOIN sleeper_matchups m2
    ON m2.league_id = m1.league_id
   AND m2.week = m1.week
   AND m2.matchup_id = m1.matchup_id
   AND m2.roster_id != m1.roster_id;

-- REMOVED 11/8/26: fantasy_matchup_points, fantasy_matchup_team_totals and fantasy_matchup_results used to be defined here. They were
-- retired (they scored from this project's own game_fantasy_scores_weekly, not Sleeper's recorded points) and no longer exist in the
-- database; historical_matchup_results and historical_standings replace them. Re-running the old file would have recreated them.

-- =========================
-- Verification
-- =========================

-- Every roster should have exactly one opponent per week (or NULL on a bye) -- duplicates here would mean matchup_id isn't
-- uniquely pairing two rosters. Should return no rows.
SELECT league_id, week, roster_id, COUNT(*) AS row_count
FROM fantasy_matchups
GROUP BY league_id, week, roster_id
HAVING COUNT(*) > 1;
