-- Naive baseline candidate B for the Phase 2 backtest: the median
-- fantasy_score across the ownable pool's games. "Lock anything above
-- this, no modeling at all" is the literal honest version of "a score
-- with better than a coin-flip chance of being good" -- kept as a
-- SEPARATE comparison strategy for the backtest, never folded into
-- game_lock_signal's actual decision logic (that's what caused the
-- LOCK-anyway bug fixed earlier).

SELECT
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gls.fantasy_score) AS median_fantasy_score
FROM game_lock_signal gls;

-- Same, split by variance_bucket -- worth checking whether the median
-- score meaningfully differs by bucket before deciding whether the naive
-- baseline should be one flat number or bucketed like percentage_to_lock
SELECT
    variance_bucket,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY fantasy_score) AS median_fantasy_score
FROM game_lock_signal
GROUP BY variance_bucket
ORDER BY variance_bucket;
