-- One-time cleanup: removes the old variance-bucket-based objects,
-- fully superseded by the tier-based redesign (8/9/26). Safe to run
-- once, any time before or after deploying the new percentage_to_lock.sql.

DROP FUNCTION IF EXISTS hold_win_probability(BIGINT, INTEGER);
DROP TABLE IF EXISTS hold_value_curve_params CASCADE;
DROP VIEW IF EXISTS player_variance_buckets CASCADE;

-- Confirm they're gone
SELECT relname, relkind FROM pg_class
WHERE relname IN ('hold_value_curve_params', 'player_variance_buckets');
-- EXPECT: 0 rows
