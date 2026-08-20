-- Schema validation for Sleeper's JSONB payloads: scoring_settings and
-- transactions.adds/drops. Addresses docs/architecture_risks.md #3 --
-- these are currently trusted structurally with no validation that
-- Sleeper's shape hasn't changed. Two real bugs have already come from
-- this class of problem:
--   - fga float-precision artifact in scoring_settings (confirmed
--     8/13/26, see sleeper_scoring_constants_view.sql's header)
--   - jsonb_typeof null-handling bug in transactions.adds/drops
--     (confirmed 8/14/26, see transactions_view.sql's header)
-- A third instance -- Sleeper renaming or retyping a key -- would fail
-- the same way: silently, only discovered when someone happens to
-- inspect that specific field. This script makes that inspection
-- routine instead of accidental.
--
-- NOT covered here: players_points. Its consuming file/view wasn't
-- available when this was written -- add a matching block below once
-- that source is in hand, following the same jsonb_typeof pattern
-- rather than guessing its shape.
--
-- Uses jsonb_typeof(...) throughout, never a raw cast -- a cast that
-- fails (e.g. ::numeric on a string, ::integer on a decimal) aborts
-- the whole query with an opaque error instead of reporting which row
-- and which key broke. jsonb_typeof always returns cleanly (or SQL
-- NULL for a missing key), so every check below completes and reports
-- every failure at once, not just the first one Postgres happens to
-- hit.
--
-- Expect 0 rows from every SELECT below. Any row returned is a real
-- shape drift worth investigating before it silently breaks
-- game_fantasy_scores or transaction_players.

-- =========================
-- scoring_settings
-- =========================

-- The 14 keys sleeper_scoring_constants_view.sql maps into named
-- columns. Each must exist and be JSON type 'number' -- a missing key
-- makes the ->'key' lookup return SQL NULL, which jsonb_typeof also
-- returns NULL for, correctly failing the check rather than silently
-- passing.
SELECT
    league_id, season,
    jsonb_typeof(scoring_settings->'pts')  AS pts_type,
    jsonb_typeof(scoring_settings->'reb')  AS reb_type,
    jsonb_typeof(scoring_settings->'oreb') AS oreb_type,
    jsonb_typeof(scoring_settings->'ast')  AS ast_type,
    jsonb_typeof(scoring_settings->'stl')  AS stl_type,
    jsonb_typeof(scoring_settings->'blk')  AS blk_type,
    jsonb_typeof(scoring_settings->'to')   AS tov_type,
    jsonb_typeof(scoring_settings->'fgm')  AS fgm_type,
    jsonb_typeof(scoring_settings->'fga')  AS fga_type,
    jsonb_typeof(scoring_settings->'ftm')  AS ftm_type,
    jsonb_typeof(scoring_settings->'fta')  AS fta_type,
    jsonb_typeof(scoring_settings->'tpm')  AS fg3m_type,
    jsonb_typeof(scoring_settings->'dd')   AS dd_type,
    jsonb_typeof(scoring_settings->'td')   AS td_type
FROM sleeper_leagues
WHERE NOT (
    jsonb_typeof(scoring_settings->'pts')  = 'number' AND
    jsonb_typeof(scoring_settings->'reb')  = 'number' AND
    jsonb_typeof(scoring_settings->'oreb') = 'number' AND
    jsonb_typeof(scoring_settings->'ast')  = 'number' AND
    jsonb_typeof(scoring_settings->'stl')  = 'number' AND
    jsonb_typeof(scoring_settings->'blk')  = 'number' AND
    jsonb_typeof(scoring_settings->'to')   = 'number' AND
    jsonb_typeof(scoring_settings->'fgm')  = 'number' AND
    jsonb_typeof(scoring_settings->'fga')  = 'number' AND
    jsonb_typeof(scoring_settings->'ftm')  = 'number' AND
    jsonb_typeof(scoring_settings->'fta')  = 'number' AND
    jsonb_typeof(scoring_settings->'tpm')  = 'number' AND
    jsonb_typeof(scoring_settings->'dd')   = 'number' AND
    jsonb_typeof(scoring_settings->'td')   = 'number'
);

-- The 4 keys game_fantasy_scores hardcodes rather than pulling from
-- this view (bonus_pt_40p, bonus_pt_50p, bonus_ast_15p, bonus_reb_20p
-- -- see sleeper_scoring_constants_view.sql's header, decision made
-- 8/13/26 on the assumption these aren't expected to change). Existence
-- + type checked here since a hardcoded value silently going stale if
-- Sleeper ever changes these is exactly the risk this script exists
-- to catch -- but the actual EXPECTED numeric values live in
-- game_fantasy_scores_view.sql, not here; cross-check there if this
-- block ever flags a real drift.
SELECT
    league_id, season,
    jsonb_typeof(scoring_settings->'bonus_pt_40p')  AS bonus_pt_40p_type,
    jsonb_typeof(scoring_settings->'bonus_pt_50p')  AS bonus_pt_50p_type,
    jsonb_typeof(scoring_settings->'bonus_ast_15p') AS bonus_ast_15p_type,
    jsonb_typeof(scoring_settings->'bonus_reb_20p') AS bonus_reb_20p_type
FROM sleeper_leagues
WHERE NOT (
    jsonb_typeof(scoring_settings->'bonus_pt_40p')  = 'number' AND
    jsonb_typeof(scoring_settings->'bonus_pt_50p')  = 'number' AND
    jsonb_typeof(scoring_settings->'bonus_ast_15p') = 'number' AND
    jsonb_typeof(scoring_settings->'bonus_reb_20p') = 'number'
);

-- =========================
-- sleeper_transactions.adds / .drops
-- =========================

-- Top-level shape: every non-SQL-NULL adds/drops value must be either
-- JSON null (a pure drop or pure add -- valid, expected, see
-- transactions_view.sql's header on the jsonb_typeof bug this
-- represents) or a JSON object. Anything else (array, string, number,
-- bare boolean) is a real shape change transaction_players' CROSS JOIN
-- LATERAL jsonb_each_text would break on.
SELECT transaction_id, league_id, 'adds' AS field, jsonb_typeof(adds) AS actual_type
FROM sleeper_transactions
WHERE adds IS NOT NULL AND jsonb_typeof(adds) NOT IN ('object', 'null')
UNION ALL
SELECT transaction_id, league_id, 'drops' AS field, jsonb_typeof(drops) AS actual_type
FROM sleeper_transactions
WHERE drops IS NOT NULL AND jsonb_typeof(drops) NOT IN ('object', 'null');

-- Inner shape: for every object-typed adds/drops, each key should be a
-- Sleeper player_id (all-digit string) and each value an integer
-- roster_id (all-digit string before the ::integer cast
-- transaction_players applies). A regex check here, not a live cast --
-- this reports every bad key/value pair in one pass instead of the
-- CROSS JOIN LATERAL erroring out on the first one it hits.
SELECT t.transaction_id, t.league_id, 'adds' AS field, tp.key, tp.value
FROM sleeper_transactions t
CROSS JOIN LATERAL jsonb_each_text(t.adds) AS tp(key, value)
WHERE jsonb_typeof(t.adds) = 'object'
  AND (tp.key !~ '^\d+$' OR tp.value !~ '^\d+$')

UNION ALL

SELECT t.transaction_id, t.league_id, 'drops' AS field, tp.key, tp.value
FROM sleeper_transactions t
CROSS JOIN LATERAL jsonb_each_text(t.drops) AS tp(key, value)
WHERE jsonb_typeof(t.drops) = 'object'
  AND (tp.key !~ '^\d+$' OR tp.value !~ '^\d+$');
