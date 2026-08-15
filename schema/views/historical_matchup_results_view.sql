-- sleeper_matchup_points_current: dedupes sleeper_matchup_points_snapshots
-- down to the single most-recent snapshot per (league_id, week,
-- roster_id) -- during a live season with mid-week swaps there can be
-- multiple snapshots per roster/week; this is always "whatever the
-- final recorded value was."

DROP VIEW IF EXISTS sleeper_roster_labels_current CASCADE;

CREATE VIEW sleeper_roster_labels_current AS
SELECT sr.league_id, sr.roster_id, su.display_name AS current_owner_name
FROM sleeper_rosters sr
LEFT JOIN sleeper_users su ON su.league_id = sr.league_id AND su.user_id = sr.owner_id;

-- historical_matchup_results: who played who, and who won, using
-- Sleeper's own recorded points -- NOT this project's independently-
-- computed fantasy_score. Deliberately separate from
-- fantasy_matchup_results (which stays on game_fantasy_scores_weekly
-- for whatever it's needed for). See sleeper_matchup_points_snapshots's
-- header for why: a daily-lineup league means the real historical
-- winner was decided by whichever player was actually locked in on
-- each specific day, and that information no longer exists anywhere
-- except in Sleeper's own already-computed point totals.
--
-- No league_id filter -- for 2026-27 (or any week not yet played),
-- sleeper_matchup_points_snapshots simply has no rows yet, so this
-- naturally returns NULL points/result for those rows via the LEFT
-- JOINs below, same as fantasy_matchup_results already does for an
-- unplayed season. Self-updating, no hardcoded league_id list to
-- maintain.

DROP VIEW IF EXISTS historical_matchup_results CASCADE;

CREATE VIEW historical_matchup_results AS
SELECT
    fm.league_id,
    fm.week,
    fm.roster_id,
    su1.display_name AS owner_name,
    smp1.points AS team_points,
    fm.opponent_roster_id,
    su2.display_name AS opponent_owner_name,
    smp2.points AS opponent_points,
    CASE
        WHEN fm.opponent_roster_id IS NULL THEN NULL          -- bye week
        WHEN smp1.points IS NULL OR smp2.points IS NULL THEN NULL  -- not played / not synced yet
        WHEN smp1.points > smp2.points THEN 'W'
        WHEN smp1.points < smp2.points THEN 'L'
        ELSE 'T'
    END AS result
FROM fantasy_matchups fm
LEFT JOIN sleeper_matchup_points_latest smp1
    ON smp1.league_id = fm.league_id AND smp1.week = fm.week AND smp1.roster_id = fm.roster_id
LEFT JOIN sleeper_matchup_points_latest smp2
    ON smp2.league_id = fm.league_id AND smp2.week = fm.week AND smp2.roster_id = fm.opponent_roster_id
LEFT JOIN sleeper_rosters sr1
    ON sr1.league_id = fm.league_id AND sr1.roster_id = fm.roster_id
LEFT JOIN sleeper_users su1
    ON su1.league_id = sr1.league_id AND su1.user_id = sr1.owner_id
LEFT JOIN sleeper_rosters sr2
    ON sr2.league_id = fm.league_id AND sr2.roster_id = fm.opponent_roster_id
LEFT JOIN sleeper_users su2
    ON su2.league_id = sr2.league_id AND su2.user_id = sr2.owner_id;

-- historical_standings: same regular-season-only (week <= 21) logic as
-- fantasy_matchup_results' standings query, sourced from
-- historical_matchup_results instead.

DROP VIEW IF EXISTS historical_standings CASCADE;

CREATE VIEW historical_standings AS
SELECT sl.season, hmr.owner_name,
       COUNT(*) FILTER (WHERE hmr.result = 'W') AS wins,
       COUNT(*) FILTER (WHERE hmr.result = 'L') AS losses,
       COUNT(*) FILTER (WHERE hmr.result = 'T') AS ties,
       ROUND(SUM(hmr.team_points), 2) AS total_points
FROM historical_matchup_results hmr
JOIN sleeper_leagues sl ON sl.league_id = hmr.league_id
WHERE hmr.week <= 21
GROUP BY sl.season, hmr.owner_name
ORDER BY sl.season DESC, wins DESC, total_points DESC;

-- =========================
-- Verification
-- =========================

-- Week 1, 2024 -- eyeball every score against the app screenshot
-- directly: should show Sam Zesti/sweetdiddlydee 419.55 beating
-- Yaak0v 303.60, TommyTableSalsa 415.50 beating SeanKelly13 383.25,
-- etc.
SELECT hmr.week, hmr.owner_name, hmr.team_points, hmr.opponent_owner_name,
       hmr.opponent_points, hmr.result
FROM historical_matchup_results hmr
JOIN sleeper_leagues sl ON sl.league_id = hmr.league_id
WHERE sl.season = '2024' AND hmr.week = 1
ORDER BY hmr.team_points DESC;

-- 2025 all-time regular-season standings -- should match exactly:
-- sweetdiddlydee 18-3, CountyShirriff 15-6, Folger11 14-7, Crash374
-- 13-8, Hendo64 12-9, cocohebbles 10-11, Pete1771 9-12, TommyTableSalsa
-- 8-13, Yaak0v 5-16, SeanKelly13 1-20
SELECT owner_name, wins, losses, ties, total_points
FROM historical_standings
WHERE season = '2025'
ORDER BY wins DESC;

-- 2024 all-time regular-season standings -- should match exactly:
-- TommyTableSalsa 18-3, Folger11 15-5-1, Hendo64 12-9, sweetdiddlydee
-- 12-9, CountyShirriff 12-9, SeanKelly13 12-9, cocohebbles 8-13,
-- Yaak0v 6-15, Crash374 5-15-1, Pete1771 4-17
SELECT owner_name, wins, losses, ties, total_points
FROM historical_standings
WHERE season = '2024'
ORDER BY wins DESC;

-- Combined all-time (2024+2025) -- should match exactly: sweetdiddlydee
-- 30-12, Folger11 29-12-1, CountyShirriff 27-15, TommyTableSalsa 26-16,
-- Hendo64 24-18, Crash374 18-23-1, cocohebbles 18-24, Pete1771 13-29,
-- SeanKelly13 13-29, Yaak0v 11-31
SELECT owner_name,
       SUM(wins) AS wins, SUM(losses) AS losses, SUM(ties) AS ties,
       ROUND(SUM(total_points), 2) AS total_points
FROM historical_standings
GROUP BY owner_name
ORDER BY wins DESC;

ORDER BY s.week, s.synced_at;

-- sweetdiddlydee's full 2025 season, week by week, real opponent + real result
SELECT hmr.week, hmr.owner_name, hmr.team_points, hmr.opponent_owner_name,
       hmr.opponent_points, hmr.result
FROM historical_matchup_results hmr
JOIN sleeper_leagues sl ON sl.league_id = hmr.league_id
WHERE sl.season = '2025' AND hmr.owner_name = 'sweetdiddlydee' AND hmr.week <= 21
ORDER BY hmr.week;

-- 2024 — did any actual week-1 starter get dropped from roster 8 after that week?
SELECT tpd.created, tpd.action, tpd.player_name, tpd.sleeper_player_id
FROM transaction_players_detail tpd
WHERE tpd.league_id = '1113487058661744640' AND tpd.roster_id = 8
  AND tpd.sleeper_player_id IN ('1085','2309','1845','2455','1595','1525','1883','1697','1380')
ORDER BY tpd.created;

-- 2025-26 — same check, this season's actual week-1 starters
SELECT tpd.created, tpd.action, tpd.player_name, tpd.sleeper_player_id
FROM transaction_players_detail tpd
WHERE tpd.league_id = '1214984705477185536' AND tpd.roster_id = 8
  AND tpd.sleeper_player_id IN ('1085','2157','1845','2285','1380','1308','2455','2142','1697')
ORDER BY tpd.created;
