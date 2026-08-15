-- fantasy_matchups: pairs each roster with its opponent for a given
-- week, self-joining sleeper_matchups on (league_id, week, matchup_id).
-- LEFT JOIN, not INNER -- a bye week (odd number of teams, or a
-- matchup_id with no counterpart row) still produces a row with
-- opponent_roster_id = NULL instead of disappearing.

DROP VIEW IF EXISTS fantasy_matchups CASCADE;

CREATE VIEW fantasy_matchups AS
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

-- fantasy_matchup_points: per-starter, per-game fantasy_score for
-- every roster/week, computed from THIS project's own
-- game_fantasy_scores_weekly -- never from Sleeper's own points
-- (upsert_matchups() deliberately never stores those, see
-- backfill_sleeper_league.py). A starter with 2 games that week
-- produces 2 rows (summed in fantasy_matchup_team_totals below); a
-- starter with 0 games that week produces 1 row with fantasy_score
-- NULL (excluded by SUM downstream, not treated as a 0).
--
-- ASSUMPTION flagged for review, not yet confirmed: joins on
-- gfsw.week_number = sm.week, i.e. assumes Sleeper's own week numbers
-- line up 1:1 with this project's fantasy_weeks.week_number. Plausible
-- since fantasy_weeks was built to mirror this league's actual 24-week
-- schedule (MAX_WEEK = 24 in backfill_sleeper_league.py), but not
-- verified here -- run the verification query below before trusting
-- team totals.
--
-- REAL LIMITATION, accepted: sleeper_matchups is upserted per
-- (league_id, week, roster_id) -- one row, overwritten on every sync.
-- If starters were swapped mid-week, only the most-recently-synced
-- lineup survives; there's no day-by-day history of who actually
-- started which specific day. This view reflects "if the
-- currently-synced starters had played all their games that week,"
-- not true day-by-day scoring history.

DROP VIEW IF EXISTS fantasy_matchup_points CASCADE;

CREATE VIEW fantasy_matchup_points AS
SELECT
    sm.league_id,
    sm.week,
    sm.roster_id,
    sp AS sleeper_player_id,
    spc.nba_player_id,
    p.full_name AS player_name,
    gfsw.game_id,
    gfsw.game_date,
    gfsw.fantasy_score
FROM sleeper_matchups sm
JOIN sleeper_leagues sl
    ON sl.league_id = sm.league_id
CROSS JOIN LATERAL unnest(sm.starters) AS sp
LEFT JOIN sleeper_player_crosswalk spc
    ON spc.sleeper_player_id = sp
LEFT JOIN game_fantasy_scores_weekly gfsw
    ON gfsw.player_id = spc.nba_player_id
   AND gfsw.season_id = ('2' || sl.season)
   AND gfsw.week_number = sm.week
LEFT JOIN players p
    ON p.player_id = spc.nba_player_id
WHERE sp != '0';  -- Sleeper's placeholder for an empty starter slot,
                   -- not a real player_id -- confirmed 8/13/26 this
                   -- accounted for 211 of 259 rows in the crosswalk
                   -- gap check (league hasn't started matchups yet,
                   -- so most starter slots are still genuinely empty).
                   -- sleeper_player_id is TEXT, so this must be a
                   -- string comparison, not sp != 0.

-- fantasy_matchup_team_totals: fantasy_matchup_points summed to one
-- row per roster per week.

DROP VIEW IF EXISTS fantasy_matchup_team_totals CASCADE;

CREATE VIEW fantasy_matchup_team_totals AS
SELECT
    fmp.league_id,
    fmp.week,
    fmp.roster_id,
    su.display_name AS owner_name,
    SUM(fmp.fantasy_score) AS team_fantasy_points,
    COUNT(DISTINCT fmp.sleeper_player_id) AS starters_used,
    COUNT(*) FILTER (WHERE fmp.nba_player_id IS NULL) AS unmatched_starter_slots
FROM fantasy_matchup_points fmp
LEFT JOIN sleeper_rosters sr
    ON sr.league_id = fmp.league_id AND sr.roster_id = fmp.roster_id
LEFT JOIN sleeper_users su
    ON su.league_id = fmp.league_id AND su.user_id = sr.owner_id
GROUP BY fmp.league_id, fmp.week, fmp.roster_id, su.display_name;

-- fantasy_matchup_results: fantasy_matchups + fantasy_matchup_team_totals
-- joined into a standings-ready W/L view. result is NULL for bye weeks
-- (opponent_roster_id IS NULL).

DROP VIEW IF EXISTS fantasy_matchup_results CASCADE;

CREATE VIEW fantasy_matchup_results AS
SELECT
    fm.league_id,
    fm.week,
    fm.roster_id,
    t1.owner_name,
    t1.team_fantasy_points,
    fm.opponent_roster_id,
    t2.owner_name AS opponent_owner_name,
    t2.team_fantasy_points AS opponent_fantasy_points,
    CASE
        WHEN fm.opponent_roster_id IS NULL THEN NULL           -- bye week
        WHEN t1.team_fantasy_points IS NULL
          OR t2.team_fantasy_points IS NULL THEN NULL          -- no fantasy_score data
                                                                 -- for one or both sides yet
                                                                 -- (e.g. season hasn't
                                                                 -- started) -- NOT the same
                                                                 -- thing as a real tie.
                                                                 -- Confirmed 8/13/26: without
                                                                 -- this check, NULL > NULL /
                                                                 -- NULL < NULL both evaluate
                                                                 -- to NULL/unknown, so neither
                                                                 -- WHEN below fires and it was
                                                                 -- silently falling through to
                                                                 -- ELSE 'T' -- every roster-week
                                                                 -- in the not-yet-started
                                                                 -- 2026-27 season was showing
                                                                 -- as a tie.
        WHEN t1.team_fantasy_points > t2.team_fantasy_points THEN 'W'
        WHEN t1.team_fantasy_points < t2.team_fantasy_points THEN 'L'
        ELSE 'T'
    END AS result
FROM fantasy_matchups fm
LEFT JOIN fantasy_matchup_team_totals t1
    ON t1.league_id = fm.league_id AND t1.week = fm.week AND t1.roster_id = fm.roster_id
LEFT JOIN fantasy_matchup_team_totals t2
    ON t2.league_id = fm.league_id AND t2.week = fm.week AND t2.roster_id = fm.opponent_roster_id;

-- =========================
-- Verification
-- =========================

-- ASSUMPTION CHECK (run this first): confirms Sleeper's week numbers
-- actually line up with fantasy_weeks' week numbers, for the current
-- league. Pick one week, compare its fantasy_weeks date range against
-- the game_dates actually showing up in fantasy_matchup_points for
-- that week -- dates should fall inside the range, not before/after it.
SELECT fw.week_number, fw.week_start_date, fw.week_end_date,
       MIN(fmp.game_date) AS earliest_game_seen,
       MAX(fmp.game_date) AS latest_game_seen
FROM fantasy_matchup_points fmp
JOIN sleeper_leagues sl ON sl.league_id = fmp.league_id
JOIN fantasy_weeks fw
    ON fw.season_id = ('2' || sl.season) AND fw.week_number = fmp.week
WHERE fmp.week = 1
GROUP BY fw.week_number, fw.week_start_date, fw.week_end_date;

-- Every roster should have exactly one opponent per week (or NULL on
-- a bye) -- duplicates here would mean matchup_id isn't uniquely
-- pairing two rosters
SELECT league_id, week, roster_id, COUNT(*) AS row_count
FROM fantasy_matchups
GROUP BY league_id, week, roster_id
HAVING COUNT(*) > 1;

-- Crosswalk gap check -- same expected-NULLs-for-rookies pattern as
-- roster_ownership and transaction_players_detail
SELECT league_id, week, roster_id, sleeper_player_id
FROM fantasy_matchup_points
WHERE nba_player_id IS NULL;

-- Current week's full slate, W/L and points -- eyeball for plausibility.
-- league_id/season shown explicitly: this table spans all 3 seasons in
-- the dynasty chain (2024, 2025, 2026-27), and "week 24" exists once
-- PER SEASON -- confirmed 8/13/26 that omitting league_id here made
-- three different seasons' week-24 rows look like conflicting
-- duplicate matchups for the same team. Filter to one league_id (see
-- sleeper_current_league from step 4) for a single season's slate.
SELECT sl.season, fmr.week, fmr.owner_name, fmr.team_fantasy_points,
       fmr.opponent_owner_name, fmr.opponent_fantasy_points, fmr.result
FROM fantasy_matchup_results fmr
JOIN sleeper_leagues sl ON sl.league_id = fmr.league_id
ORDER BY sl.season DESC, fmr.week DESC, fmr.roster_id
LIMIT 20;

-- Per-season, REGULAR-SEASON-ONLY standings (matches Sleeper's own
-- displayed win/loss record) -- confirmed 8/13/26 against the actual
-- League History page: playoff weeks (22-24) do NOT count toward the
-- standings record at all. Every team's all-time record sums to
-- exactly 42 games (21 x 2 completed seasons), confirming the
-- regular-season cutoff is week <= 21, not all 24 -- the earlier
-- version of this query (summing all 24 weeks) was wrong, not just
-- unclear.
SELECT sl.season, fmr.owner_name,
       COUNT(*) FILTER (WHERE fmr.result = 'W') AS wins,
       COUNT(*) FILTER (WHERE fmr.result = 'L') AS losses,
       COUNT(*) FILTER (WHERE fmr.result = 'T') AS ties,
       ROUND(SUM(fmr.team_fantasy_points), 2) AS total_points
FROM fantasy_matchup_results fmr
JOIN sleeper_leagues sl ON sl.league_id = fmr.league_id
WHERE fmr.week <= 21
GROUP BY sl.season, fmr.owner_name
ORDER BY sl.season DESC, wins DESC, total_points DESC;

SELECT fw.week_number, fw.week_start_date, fw.week_end_date,
       MIN(fmp.game_date) AS earliest_game_seen,
       MAX(fmp.game_date) AS latest_game_seen
FROM fantasy_matchup_points fmp
JOIN sleeper_leagues sl ON sl.league_id = fmp.league_id
JOIN fantasy_weeks fw
    ON fw.season_id = ('2' || sl.season) AND fw.week_number = fmp.week
WHERE sl.season = '2025' AND fmp.week = 1
GROUP BY fw.week_number, fw.week_start_date, fw.week_end_date;

SELECT fmr.week, fmr.owner_name, fmr.team_fantasy_points,
       fmr.opponent_owner_name, fmr.opponent_fantasy_points, fmr.result
FROM fantasy_matchup_results fmr
JOIN sleeper_leagues sl ON sl.league_id = fmr.league_id
WHERE sl.season = '2025' AND fmr.owner_name = 'Hendo64' AND fmr.week <= 21
ORDER BY fmr.week;

-- All-time (2024+2025 combined) regular-season-only record -- should
-- match Sleeper's own "All Time Standings" page exactly for
-- wins/losses/ties: sweetdiddlydee 30-12, Folger11 29-12-1,
-- CountyShirriff 27-15, TommyTableSalsa 26-16, Hendo64 24-18,
-- Crash374 18-23-1, cocohebbles 18-24, Pete1771 13-29, SeanKelly13
-- 13-29, Yaak0v 11-31 (confirmed 8/13/26 from the app). total_points
-- (PF) may NOT match Sleeper's PF exactly even if W/L does -- two
-- known, accepted reasons: (1) technical/flagrant foul penalties
-- aren't in this project's fantasy_score, since game_logs has no
-- columns to distinguish them from ordinary personal fouls (see
-- game_fantasy_scores_view.sql header), and (2) sleeper_matchups only
-- keeps the most-recently-synced starters per week, not true
-- day-by-day lineup history (see fantasy_matchup_points header) -- a
-- mid-week lineup swap could shift which games get counted here vs
-- what Sleeper actually scored live that day.
SELECT fmr.owner_name,
       COUNT(*) FILTER (WHERE fmr.result = 'W') AS career_wins,
       COUNT(*) FILTER (WHERE fmr.result = 'L') AS career_losses,
       COUNT(*) FILTER (WHERE fmr.result = 'T') AS career_ties,
       ROUND(SUM(fmr.team_fantasy_points), 2) AS career_total_points
FROM fantasy_matchup_results fmr
WHERE fmr.week <= 21
GROUP BY fmr.owner_name
ORDER BY career_wins DESC, career_total_points DESC;
