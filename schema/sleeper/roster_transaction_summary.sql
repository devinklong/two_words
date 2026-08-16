-- roster_transaction_summary: successful (status='complete') transaction
-- counts per roster, broken out by type -- deliberately grouped by the
-- real `type` column values rather than hardcoding a guess at Sleeper's
-- exact type vocabulary ('waiver', 'trade', 'free_agent', etc. --
-- confirmed distinct values not yet checked against this project's own
-- data as of 8/15/26). Excludes failed transactions entirely per
-- project decision (8/15/26) -- failed waiver claims aren't counted as
-- real roster activity.
--
-- Roster_id-pure by design, matching the project's architecture rule --
-- resolve owner names via a join to sleeper_roster_labels_current at
-- query time, not stored here.

DROP VIEW IF EXISTS roster_transaction_summary;

CREATE VIEW roster_transaction_summary AS
SELECT
    tp.league_id,
    tp.roster_id,
    t.type,
    COUNT(DISTINCT tp.transaction_id) AS transaction_count
FROM transaction_players tp
JOIN sleeper_transactions t ON t.transaction_id = tp.transaction_id
WHERE t.status = 'complete'
GROUP BY tp.league_id, tp.roster_id, t.type;

-- =========================
-- Detailed transaction log -- season, owner, type, add/drop, date, one
-- row per event. Reuses transaction_players_detail (already resolves
-- player_name via the crosswalk), just adds season + owner on top.
-- =========================

SELECT
    sl.season,
    rl.current_owner_name,
    tpd.transaction_type AS type,
    tpd.action,
    tpd.player_name,
    tpd.created AS date
FROM transaction_players_detail tpd
JOIN sleeper_leagues sl ON sl.league_id = tpd.league_id
JOIN sleeper_roster_labels_current rl
    ON rl.league_id = tpd.league_id AND rl.roster_id = tpd.roster_id
WHERE tpd.status = 'complete'
ORDER BY tpd.created DESC;

-- =========================
-- Verification
-- =========================

-- What type values actually exist -- run this FIRST, before trusting
-- any type-specific breakdown below.
SELECT DISTINCT type FROM sleeper_transactions ORDER BY type;

-- Per-season breakdown, one row per (owner, type)
SELECT sl.season, rl.current_owner_name, rts.type, rts.transaction_count
FROM roster_transaction_summary rts
JOIN sleeper_leagues sl ON sl.league_id = rts.league_id
JOIN sleeper_roster_labels_current rl
    ON rl.league_id = rts.league_id AND rl.roster_id = rts.roster_id
ORDER BY sl.season DESC, rl.current_owner_name, rts.type;

-- All-time (both seasons combined), one row per (owner, type) --
-- this is the one that answers "each team's successful waiver claims
-- and successful trades" directly, once type values are confirmed above
SELECT rl.current_owner_name, rts.type, SUM(rts.transaction_count) AS total_count
FROM roster_transaction_summary rts
JOIN sleeper_roster_labels_current rl
    ON rl.roster_id = rts.roster_id
    AND rl.league_id = (SELECT league_id FROM sleeper_current_league)
GROUP BY rl.current_owner_name, rts.type
ORDER BY rl.current_owner_name, rts.type;


SELECT t.type, tp.action, COUNT(*)
FROM transaction_players tp
JOIN sleeper_transactions t ON t.transaction_id = tp.transaction_id
WHERE t.status = 'complete'
GROUP BY t.type, tp.action
ORDER BY t.type, tp.action;