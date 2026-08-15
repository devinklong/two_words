-- transaction_players: one row per player added or dropped in a
-- transaction, unnesting sleeper_transactions.adds/drops (both are
-- JSONB maps of {sleeper_player_id: roster_id}). Handles waivers, free
-- agent adds/drops, AND trades uniformly -- a traded player shows up as
-- an 'add' row for the receiving roster and a 'drop' row for the
-- losing roster, since that's how Sleeper's adds/drops JSON already
-- represents trades. No special-casing needed per transaction type.
--
-- Filters on jsonb_typeof(...) = 'object', not IS NOT NULL/!= '{}' --
-- confirmed 8/14/26 that a pure drop (no adds at all, e.g. losing a
-- waiver claim or a straight cut) gets adds stored as a literal JSON
-- null (from json.dumps(None) in upsert_transactions()), which is NOT
-- SQL NULL and passed the old check, then broke jsonb_each_text
-- ("cannot call jsonb_each_text on a non-object"). jsonb_typeof
-- correctly excludes SQL NULL, JSON null, and non-object types in one
-- check.

DROP VIEW IF EXISTS transaction_players CASCADE;

CREATE VIEW transaction_players AS
SELECT
    t.transaction_id,
    t.league_id,
    t.type AS transaction_type,
    t.status,
    t.week,
    t.created,
    'add'::text AS action,
    tp.key AS sleeper_player_id,
    tp.value::integer AS roster_id
FROM sleeper_transactions t
CROSS JOIN LATERAL jsonb_each_text(t.adds) AS tp(key, value)
WHERE jsonb_typeof(t.adds) = 'object'

UNION ALL

SELECT
    t.transaction_id,
    t.league_id,
    t.type AS transaction_type,
    t.status,
    t.week,
    t.created,
    'drop'::text AS action,
    tp.key AS sleeper_player_id,
    tp.value::integer AS roster_id
FROM sleeper_transactions t
CROSS JOIN LATERAL jsonb_each_text(t.drops) AS tp(key, value)
WHERE jsonb_typeof(t.drops) = 'object';

-- transaction_players_detail: roster_id-pure + player identity (via
-- crosswalk -- that's a name/identity lookup, not an ownership-history
-- problem, safe to keep here). NO owner_name column -- confirmed
-- 8/14/26 the same current-state-applied-to-history issue found in
-- the matchup/standings views applies here too: sleeper_rosters is
-- upserted, current-ownership-only, so joining it directly into a
-- transaction log stamps TODAY's owner onto a possibly-old
-- transaction. See historical_matchup_results_view.sql's architecture
-- note and sleeper_roster_labels_current for the display-layer split.

DROP VIEW IF EXISTS transaction_players_detail CASCADE;

CREATE VIEW transaction_players_detail AS
SELECT
    tp.transaction_id,
    tp.league_id,
    tp.transaction_type,
    tp.status,
    tp.week,
    tp.created,
    tp.action,
    tp.roster_id,
    tp.sleeper_player_id,
    p.full_name AS player_name
FROM transaction_players tp
LEFT JOIN sleeper_player_crosswalk spc
    ON spc.sleeper_player_id = tp.sleeper_player_id
LEFT JOIN players p
    ON p.player_id = spc.nba_player_id;

-- transaction_players_detail_labeled: convenience view for humans.
-- owner_name reflects TODAY's roster_id->owner mapping via
-- sleeper_roster_labels_current (see historical_matchup_results_view.sql)
-- -- it may NOT be who actually owned this roster when the transaction
-- happened if ownership ever changed since. Everything else in this
-- view is unaffected by and independent of the label.

DROP VIEW IF EXISTS transaction_players_detail_labeled CASCADE;

CREATE VIEW transaction_players_detail_labeled AS
SELECT
    tpd.*,
    rl.current_owner_name AS owner_name
FROM transaction_players_detail tpd
LEFT JOIN sleeper_roster_labels_current rl
    ON rl.league_id = tpd.league_id AND rl.roster_id = tpd.roster_id;

-- =========================
-- Verification
-- =========================

-- Row count sanity: every add/drop key across every transaction should
-- show up here, base view + detail view should match exactly
SELECT COUNT(*) FROM transaction_players;
SELECT COUNT(*) FROM transaction_players_detail;

-- Trades should show paired add+drop rows for the same
-- sleeper_player_id within the same transaction_id (one roster's add
-- is another roster's drop) -- spot check one trade
SELECT transaction_id, action, owner_name, sleeper_player_id, player_name
FROM transaction_players_detail_labeled
WHERE transaction_type = 'trade'
ORDER BY transaction_id, sleeper_player_id, action
LIMIT 20;

-- Crosswalk gap check, same pattern as roster_ownership -- expect
-- some NULLs here for very recent rookie adds until nba_api's static
-- list catches up
SELECT sleeper_player_id, action, roster_id, transaction_type, created
FROM transaction_players_detail
WHERE player_name IS NULL
ORDER BY created DESC;

-- Recent activity feed, most-recent-first -- eyeball that names,
-- actions, and transaction types look plausible together. owner_name
-- here reflects CURRENT ownership (see transaction_players_detail_labeled's
-- header) -- fine for a recent-activity feed, worth remembering if
-- ever reused for older history.
SELECT created, transaction_type, action, owner_name, player_name
FROM transaction_players_detail_labeled
ORDER BY created DESC
LIMIT 20;

-- Waiver/add-drop volume per owner -- useful gut check for who's
-- actually working the wire. Same current-ownership caveat as above.
SELECT owner_name, action, COUNT(*) AS move_count
FROM transaction_players_detail_labeled
GROUP BY owner_name, action
ORDER BY owner_name, action;

-- Find a roster with zero transactions all season in 2025-26
SELECT sr.roster_id, COUNT(tp.transaction_id) AS transaction_count
FROM sleeper_rosters sr
LEFT JOIN transaction_players tp
    ON tp.league_id = sr.league_id AND tp.roster_id = sr.roster_id
WHERE sr.league_id = '1214984705477185536'
GROUP BY sr.roster_id
ORDER BY transaction_count ASC;

SELECT roster_id, settings->'reserve' AS ir_slot
FROM sleeper_rosters
WHERE league_id = '1214984705477185536'
ORDER BY roster_id;