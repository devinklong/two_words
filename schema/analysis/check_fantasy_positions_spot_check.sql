-- schema/analysis/check_fantasy_positions_spot_check.sql
--
-- Spot-check for sleeper_player_fantasy_positions after a crosswalk
-- rebuild -- confirms real multi-eligible players actually landed with
-- more than one row, and DEF never leaked in.
--
-- Herbert Jones is a deliberate double check: he's one of the 4 players
-- flagged during the original 68%-multi-eligible investigation whose
-- crosswalk `sleeper_position` (singular field) doesn't even match a
-- value in his own `fantasy_positions` array -- so this also confirms
-- that drift didn't carry over into the new table.
--
-- Follow-up (8/24/26): confirmed Herbert Jones's PF/{SF,SG} mismatch is
-- real Sleeper-side data drift, not a sync bug -- `position` and
-- `fantasy_positions` are separately maintained on Sleeper's end and
-- nothing enforces one being a subset of the other. Checking the other
-- 3 flagged-but-not-investigated players from the same original pass
-- (Dyson Daniels, Quentin Grimes, Ethan Thompson) to see if it's the
-- same pattern.

SELECT
    c.sleeper_full_name,
    c.sleeper_position AS single_position_field,   -- old/existing column, for comparison
    array_agg(fp.position ORDER BY fp.position) AS eligible_positions,
    count(*) AS n_positions,
    c.sleeper_position = ANY(array_agg(fp.position)) AS single_position_is_in_eligible
FROM sleeper_player_crosswalk c
LEFT JOIN sleeper_player_fantasy_positions fp
    ON fp.sleeper_player_id = c.sleeper_player_id
WHERE c.sleeper_full_name IN (
    'Herbert Jones', 'Dyson Daniels', 'Quentin Grimes', 'Ethan Thompson'
)
GROUP BY c.sleeper_full_name, c.sleeper_position
ORDER BY c.sleeper_full_name;

-- Sanity checks across the whole table, not just the two named players:

-- 1. DEF should never appear -- filtered out at sync time.
SELECT count(*) AS def_rows_should_be_zero
FROM sleeper_player_fantasy_positions
WHERE position = 'DEF';

-- 2. Every position value should be one of the 5 confirmed real ones.
SELECT DISTINCT position
FROM sleeper_player_fantasy_positions
WHERE position NOT IN ('C', 'PF', 'PG', 'SF', 'SG');

-- 3. Overall multi-eligibility rate -- should land near the 68% found
--    during the original investigation (394-player sample, 8/23/26).
SELECT
    round(
        100.0 * count(*) FILTER (WHERE n_positions > 1) / count(*), 1
    ) AS pct_multi_eligible
FROM (
    SELECT sleeper_player_id, count(*) AS n_positions
    FROM sleeper_player_fantasy_positions
    GROUP BY sleeper_player_id
) per_player;
