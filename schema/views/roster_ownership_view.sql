-- sleeper_current_league: resolves "current" dynasty league_id dynamically
-- instead of hardcoding one. Since this is a dynasty league, the chain
-- (previous_league_id) never really resets year to year — so "current"
-- is just whichever league_id isn't anyone's previous_league_id (the
-- head of the chain). Self-updating every season: no rewrite needed
-- when the 2027-28 league_id shows up next year, or once matchups
-- actually start for 2026-27.
--
-- Confirmed 8/13/26: rosters count as "current" as soon as the rookie
-- draft happens, even before matchups start — the 2026-27 league is
-- correctly "current" under this definition the moment it exists,
-- regardless of matchup/week status.

DROP VIEW IF EXISTS sleeper_current_league CASCADE;

CREATE VIEW sleeper_current_league AS
SELECT sl.*
FROM sleeper_leagues sl
WHERE NOT EXISTS (
    SELECT 1 FROM sleeper_leagues sl2
    WHERE sl2.previous_league_id = sl.league_id
);

-- roster_ownership: one row per rostered player in the current league.
-- Unnests sleeper_rosters.players[] (full roster) and cross-references
-- starters[] for a starter/bench flag. Only covers what's actually in
-- sleeper_rosters — no separate taxi/IR distinction, since the raw
-- table doesn't carry one beyond players[] vs starters[].
--
-- LEFT JOINs throughout: a player not yet in sleeper_player_crosswalk
-- (rare, but possible right after a rookie draft if
-- build_sleeper_player_crosswalk.py hasn't been re-run since) shows up
-- with nba_player_id/player_name NULL instead of silently dropping the
-- roster slot — surfaces the gap instead of hiding it.

DROP VIEW IF EXISTS roster_ownership CASCADE;

CREATE VIEW roster_ownership AS
SELECT
    sr.league_id,
    sr.roster_id,
    su.display_name AS owner_name,
    sr.owner_id,
    sleeper_pid AS sleeper_player_id,
    spc.nba_player_id,
    p.full_name AS player_name,
    (sleeper_pid = ANY(sr.starters)) AS is_starter
FROM sleeper_rosters sr
JOIN sleeper_current_league scl
    ON scl.league_id = sr.league_id
LEFT JOIN sleeper_users su
    ON su.league_id = sr.league_id AND su.user_id = sr.owner_id
CROSS JOIN LATERAL unnest(sr.players) AS sleeper_pid
LEFT JOIN sleeper_player_crosswalk spc
    ON spc.sleeper_player_id = sleeper_pid
LEFT JOIN players p
    ON p.player_id = spc.nba_player_id;

-- =========================
-- Verification
-- =========================

-- Should return exactly 1 row — if 0 or 2+, the chain logic is wrong
-- (either broken previous_league_id links, or two live chains)
SELECT COUNT(*) AS current_league_count FROM sleeper_current_league;
SELECT league_id, season, status FROM sleeper_current_league;

-- Row count sanity: should be roster_count * roster size (~15-16
-- players/roster for a dynasty league w/ taxi+bench). 10 rosters ->
-- expect roughly 150-160 rows total.
SELECT COUNT(*) AS total_rostered_players FROM roster_ownership;
SELECT owner_name, COUNT(*) AS roster_size
FROM roster_ownership
GROUP BY owner_name
ORDER BY owner_name;

-- Crosswalk gap check: any rostered player NOT matched to an
-- nba_player_id. Should be 0 rows normally — a nonzero result after a
-- rookie draft means build_sleeper_player_crosswalk.py needs a re-run
-- to pick up the newly drafted rookies.
SELECT sleeper_player_id, owner_name
FROM roster_ownership
WHERE nba_player_id IS NULL;

-- Starter/bench split per team — should roughly match
-- sleeper_leagues.roster_positions (starting lineup size) for
-- is_starter = true, remainder bench
SELECT owner_name, is_starter, COUNT(*) AS player_count
FROM roster_ownership
GROUP BY owner_name, is_starter
ORDER BY owner_name, is_starter DESC;

-- Spot check: pull one full roster to eyeball names look right
SELECT owner_name, player_name, is_starter
FROM roster_ownership
WHERE owner_name = (SELECT owner_name FROM roster_ownership LIMIT 1)
ORDER BY is_starter DESC, player_name;

