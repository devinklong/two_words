-- lock_bar(): single source of truth for the self-relative lock
-- threshold formula. Centralizes what was previously hand-copied
-- across ~9 files (see docs/patch_list.md #1) -- each carrying its own
-- "MUST match game_lock_signal.sql exactly" comment, with no way to
-- verify that promise held. Mirrors percentage_to_lock.sql's
-- hold_win_probability_by_tier() pattern: a real callable function,
-- not a re-derived expression.
--
-- floor/ceiling_multiplier are PARAMETERS with defaults, not hardcoded
-- constants -- callers that just want the validated production formula
-- (floor=35, ceiling_multiplier=0.5, confirmed via the Phase 2 grid
-- search, see methodology_notes.md) call lock_bar(avg, stddev) with no
-- extra args. Callers that are deliberately testing OTHER floor/mult
-- values (grid_search_lock_decision.py, validate_lock_decision.py,
-- analyze_ceiling_penalty_by_tier.py) pass them explicitly. Same
-- function, same formula shape, both use cases covered without
-- duplicating the expression itself anywhere.
--
-- DEPLOY THIS FILE BEFORE game_lock_signal.sql -- same deploy-order
-- relationship percentage_to_lock.sql has with game_lock_signal.sql.

DROP FUNCTION IF EXISTS lock_bar(NUMERIC, NUMERIC, NUMERIC, NUMERIC);

CREATE OR REPLACE FUNCTION lock_bar(
    avg_score NUMERIC,
    stddev_score NUMERIC,
    floor_val NUMERIC DEFAULT 35,
    ceiling_multiplier NUMERIC DEFAULT 0.5
)
RETURNS NUMERIC AS $$
    SELECT GREATEST(floor_val, avg_score + ceiling_multiplier * stddev_score);
$$ LANGUAGE sql IMMUTABLE;

-- =========================
-- Verification
-- =========================

-- Matches the hand-computed value from the original formula exactly
SELECT lock_bar(50.0, 20.0) AS default_case;
-- EXPECT: GREATEST(35, 50 + 0.5*20) = GREATEST(35, 60) = 60

SELECT lock_bar(20.0, 5.0) AS floor_case;
-- EXPECT: GREATEST(35, 20 + 0.5*5) = GREATEST(35, 22.5) = 35 (floor binds)

SELECT lock_bar(50.0, 20.0, 40, 0.75) AS custom_params_case;
-- EXPECT: GREATEST(40, 50 + 0.75*20) = GREATEST(40, 65) = 65

-- Spot check against Jokic's real week 5, 2024-25 lock_bar (~79.5,
-- confirmed in game_lock_signal.sql's own verification block)
SELECT p.full_name, lock_bar(pss.avg_fantasy_score, pss.stddev_fantasy_score) AS computed_lock_bar
FROM player_season_fantasy_stats pss
JOIN players p ON p.player_id = pss.player_id
WHERE p.full_name ILIKE '%joki%' AND pss.season_id = '22024';
