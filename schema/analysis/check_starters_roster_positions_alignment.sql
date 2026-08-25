-- schema/analysis/check_starters_roster_positions_alignment.sql
--
-- Answers the v3.2 team-side blocker: does sleeper_matchups.starters[i]
-- reliably correspond to sleeper_leagues.roster_positions[i] (the real
-- slot that player filled), or is that alignment not actually safe to
-- assume? Nothing about which slot-position scores the most can be
-- built until this is answered.
--
-- KEY UNTESTED ASSUMPTION THIS QUERY ITSELF MAKES: roster_positions
-- lists all starting (non-"BN") slots FIRST, in the same order/count as
-- starters[], with bench slots trailing after. Step 1 checks that
-- assumption directly (array-length match) -- if it fails, the
-- index-based join in Step 2+ is invalid and needs rethinking.
--
-- Slot-matching rules (same convention as the earlier roster-
-- flexibility formula): UTIL accepts anyone; the generic G slot
-- accepts PG or SG; the generic F slot accepts SF or PF; every other
-- slot (PG/SG/SF/PF/C) requires an exact match against the player's
-- real eligible positions (sleeper_player_fantasy_positions, NOT the
-- unreliable singular sleeper_position field already known to be wrong
-- in several confirmed cases).
--
-- FORMATTING RULE FOR THIS FILE: every comment sits ABOVE its
-- statement, never between a CTE's closing paren and the SELECT that
-- consumes it -- a comment in that position was confirmed to break this
-- editor's statement-boundary detection (produces a false "syntax
-- error at or near LIMIT" by running the bare CTE block on its own).

-- Step 1: sanity check -- does starters[] length match the number of
-- non-BN roster_positions for that league? If any row is FALSE, stop --
-- the index alignment assumed below cannot be trusted as-is.
SELECT
    m.league_id, m.week, m.roster_id,
    array_length(m.starters, 1) AS starters_length,
    (SELECT COUNT(*) FROM unnest(l.roster_positions) AS p WHERE p != 'BN') AS non_bench_slot_count,
    array_length(m.starters, 1) =
        (SELECT COUNT(*) FROM unnest(l.roster_positions) AS p WHERE p != 'BN') AS lengths_match
FROM sleeper_matchups m
JOIN sleeper_leagues l ON l.league_id = m.league_id
WHERE m.starters IS NOT NULL
ORDER BY m.league_id, m.week, m.roster_id;

-- Step 2: the real alignment check -- for every (starter, slot_index)
-- pair, does the slot's real requirement match the player's real
-- eligible positions?
WITH starter_slots AS (
    SELECT m.league_id, m.week, m.roster_id, s.ord AS slot_index, s.sleeper_player_id
    FROM sleeper_matchups m
    CROSS JOIN LATERAL unnest(m.starters) WITH ORDINALITY AS s(sleeper_player_id, ord)
    WHERE m.starters IS NOT NULL
),
slot_labels AS (
    SELECT l.league_id, p.ord AS slot_index, p.position_label
    FROM sleeper_leagues l
    CROSS JOIN LATERAL unnest(l.roster_positions) WITH ORDINALITY AS p(position_label, ord)
    WHERE p.position_label != 'BN'
),
checked AS (
    SELECT
        ss.league_id, ss.week, ss.roster_id, ss.slot_index,
        sl.position_label AS expected_slot, ss.sleeper_player_id, spc.nba_player_id,
        array_agg(spfp.position) AS real_eligible_positions,
        CASE
            WHEN sl.position_label = 'UTIL' THEN true
            WHEN sl.position_label = 'G' THEN bool_or(spfp.position IN ('PG', 'SG'))
            WHEN sl.position_label = 'F' THEN bool_or(spfp.position IN ('SF', 'PF'))
            ELSE bool_or(spfp.position = sl.position_label)
        END AS slot_matches_eligibility
    FROM starter_slots ss
    JOIN slot_labels sl ON sl.league_id = ss.league_id AND sl.slot_index = ss.slot_index
    LEFT JOIN sleeper_player_crosswalk spc ON spc.sleeper_player_id = ss.sleeper_player_id
    LEFT JOIN sleeper_player_fantasy_positions spfp ON spfp.sleeper_player_id = ss.sleeper_player_id
    GROUP BY ss.league_id, ss.week, ss.roster_id, ss.slot_index, sl.position_label,
             ss.sleeper_player_id, spc.nba_player_id
)
SELECT * FROM checked ORDER BY league_id, week, roster_id, slot_index;

-- Step 3: summary -- overall mismatch rate. Near-0% means the alignment
-- assumption holds. Meaningfully above 0% means the mismatches need
-- inspecting (e.g. real Sleeper data drift like the singular
-- sleeper_position bug already found) before trusting anything built on
-- top of this.
WITH starter_slots AS (
    SELECT m.league_id, m.week, m.roster_id, s.ord AS slot_index, s.sleeper_player_id
    FROM sleeper_matchups m
    CROSS JOIN LATERAL unnest(m.starters) WITH ORDINALITY AS s(sleeper_player_id, ord)
    WHERE m.starters IS NOT NULL
),
slot_labels AS (
    SELECT l.league_id, p.ord AS slot_index, p.position_label
    FROM sleeper_leagues l
    CROSS JOIN LATERAL unnest(l.roster_positions) WITH ORDINALITY AS p(position_label, ord)
    WHERE p.position_label != 'BN'
),
checked AS (
    SELECT
        ss.league_id, l.season, ss.slot_index,
        CASE
            WHEN sl.position_label = 'UTIL' THEN true
            WHEN sl.position_label = 'G' THEN bool_or(spfp.position IN ('PG', 'SG'))
            WHEN sl.position_label = 'F' THEN bool_or(spfp.position IN ('SF', 'PF'))
            ELSE bool_or(spfp.position = sl.position_label)
        END AS slot_matches_eligibility
    FROM starter_slots ss
    JOIN sleeper_leagues l ON l.league_id = ss.league_id
    JOIN slot_labels sl ON sl.league_id = ss.league_id AND sl.slot_index = ss.slot_index
    LEFT JOIN sleeper_player_crosswalk spc ON spc.sleeper_player_id = ss.sleeper_player_id
    LEFT JOIN sleeper_player_fantasy_positions spfp ON spfp.sleeper_player_id = ss.sleeper_player_id
    GROUP BY ss.league_id, l.season, ss.slot_index, sl.position_label, ss.week, ss.roster_id, ss.sleeper_player_id
)
SELECT slot_matches_eligibility, COUNT(*) AS n, ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM checked
GROUP BY slot_matches_eligibility;

-- Step 4a: mismatches by season. Eligibility drift (sleeper_player_
-- fantasy_positions is current-state-only, not historical) should show
-- a clear older-worse, recent-better pattern. A real alignment bug
-- should look flat across every season instead.
WITH starter_slots AS (
    SELECT m.league_id, m.week, m.roster_id, s.ord AS slot_index, s.sleeper_player_id
    FROM sleeper_matchups m
    CROSS JOIN LATERAL unnest(m.starters) WITH ORDINALITY AS s(sleeper_player_id, ord)
    WHERE m.starters IS NOT NULL
),
slot_labels AS (
    SELECT l.league_id, p.ord AS slot_index, p.position_label
    FROM sleeper_leagues l
    CROSS JOIN LATERAL unnest(l.roster_positions) WITH ORDINALITY AS p(position_label, ord)
    WHERE p.position_label != 'BN'
),
checked AS (
    SELECT
        ss.league_id, l.season, ss.slot_index,
        CASE
            WHEN sl.position_label = 'UTIL' THEN true
            WHEN sl.position_label = 'G' THEN bool_or(spfp.position IN ('PG', 'SG'))
            WHEN sl.position_label = 'F' THEN bool_or(spfp.position IN ('SF', 'PF'))
            ELSE bool_or(spfp.position = sl.position_label)
        END AS slot_matches_eligibility
    FROM starter_slots ss
    JOIN sleeper_leagues l ON l.league_id = ss.league_id
    JOIN slot_labels sl ON sl.league_id = ss.league_id AND sl.slot_index = ss.slot_index
    LEFT JOIN sleeper_player_crosswalk spc ON spc.sleeper_player_id = ss.sleeper_player_id
    LEFT JOIN sleeper_player_fantasy_positions spfp ON spfp.sleeper_player_id = ss.sleeper_player_id
    GROUP BY ss.league_id, l.season, ss.slot_index, sl.position_label, ss.week, ss.roster_id, ss.sleeper_player_id
)
SELECT season,
       COUNT(*) FILTER (WHERE slot_matches_eligibility IS DISTINCT FROM true) AS n_mismatch,
       COUNT(*) AS n_total,
       ROUND(100.0 * COUNT(*) FILTER (WHERE slot_matches_eligibility IS DISTINCT FROM true) / COUNT(*), 2) AS pct_mismatch
FROM checked
GROUP BY season
ORDER BY season;

-- Step 4b: mismatches by slot_index. A real indexing/alignment bug
-- should cluster heavily around one or two specific slots; eligibility
-- drift should be roughly spread across all of them.
WITH starter_slots AS (
    SELECT m.league_id, m.week, m.roster_id, s.ord AS slot_index, s.sleeper_player_id
    FROM sleeper_matchups m
    CROSS JOIN LATERAL unnest(m.starters) WITH ORDINALITY AS s(sleeper_player_id, ord)
    WHERE m.starters IS NOT NULL
),
slot_labels AS (
    SELECT l.league_id, p.ord AS slot_index, p.position_label
    FROM sleeper_leagues l
    CROSS JOIN LATERAL unnest(l.roster_positions) WITH ORDINALITY AS p(position_label, ord)
    WHERE p.position_label != 'BN'
),
checked AS (
    SELECT
        ss.slot_index,
        CASE
            WHEN sl.position_label = 'UTIL' THEN true
            WHEN sl.position_label = 'G' THEN bool_or(spfp.position IN ('PG', 'SG'))
            WHEN sl.position_label = 'F' THEN bool_or(spfp.position IN ('SF', 'PF'))
            ELSE bool_or(spfp.position = sl.position_label)
        END AS slot_matches_eligibility
    FROM starter_slots ss
    JOIN slot_labels sl ON sl.league_id = ss.league_id AND sl.slot_index = ss.slot_index
    LEFT JOIN sleeper_player_crosswalk spc ON spc.sleeper_player_id = ss.sleeper_player_id
    LEFT JOIN sleeper_player_fantasy_positions spfp ON spfp.sleeper_player_id = ss.sleeper_player_id
    GROUP BY ss.slot_index, sl.position_label, ss.week, ss.roster_id, ss.sleeper_player_id
)
SELECT slot_index,
       COUNT(*) FILTER (WHERE slot_matches_eligibility IS DISTINCT FROM true) AS n_mismatch,
       COUNT(*) AS n_total,
       ROUND(100.0 * COUNT(*) FILTER (WHERE slot_matches_eligibility IS DISTINCT FROM true) / COUNT(*), 2) AS pct_mismatch
FROM checked
GROUP BY slot_index
ORDER BY slot_index;
