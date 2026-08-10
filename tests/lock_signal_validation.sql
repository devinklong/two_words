-- Manual QA sample: 15 players per season (every 10th rank by avg score,
-- 1/11/21.../141), spanning stars down through well outside the ownable
-- pool, across all 5 seasons -- deliberately broader than the pool so this
-- also validates its boundary. A sampled player with 0 rows below is
-- expected whenever they didn't clear the pool's ceiling threshold, not a
-- bug. Eyeball for anything wrong (a low-average player mostly LOCKing, a
-- star mostly PASSing, etc.) -- this is manual review, not pass/fail.

-- 1. Sample roster with pool membership flagged
WITH ranked_players AS (
    SELECT
        pss.player_id, pss.season_id, pss.full_name, pss.games_played,
        pss.avg_fantasy_score, pss.stddev_fantasy_score,
        ROW_NUMBER() OVER (
            PARTITION BY pss.season_id ORDER BY pss.avg_fantasy_score DESC, pss.player_id
        ) AS rank_by_avg_score
    FROM player_season_fantasy_stats pss
)
SELECT
    rp.season_id, rp.rank_by_avg_score, rp.full_name, rp.games_played,
    rp.avg_fantasy_score, rp.stddev_fantasy_score,
    (opp.player_id IS NOT NULL) AS in_ownable_pool
FROM ranked_players rp
LEFT JOIN ownable_player_pool opp
    ON opp.player_id = rp.player_id AND opp.season_id = rp.season_id
WHERE rp.season_id IN ('22021', '22022', '22023', '22024', '22025')
  AND rp.rank_by_avg_score IN (1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101, 111, 121, 131, 141)
ORDER BY rp.season_id, rp.rank_by_avg_score;

-- 2. Per-player-season LOCK/HOLD/PASS summary (pool members only)
WITH ranked_players AS (
    SELECT
        pss.player_id, pss.season_id, pss.full_name,
        ROW_NUMBER() OVER (
            PARTITION BY pss.season_id ORDER BY pss.avg_fantasy_score DESC, pss.player_id
        ) AS rank_by_avg_score
    FROM player_season_fantasy_stats pss
),
sample_players AS (
    SELECT player_id, season_id, full_name, rank_by_avg_score
    FROM ranked_players
    WHERE season_id IN ('22021', '22022', '22023', '22024', '22025')
      AND rank_by_avg_score IN (1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101, 111, 121, 131, 141)
)
SELECT
    sp.season_id, sp.rank_by_avg_score, sp.full_name,
    gls.lock_signal, COUNT(*) AS game_count
FROM sample_players sp
JOIN game_lock_signal gls
    ON gls.player_id = sp.player_id AND gls.season_id = sp.season_id
GROUP BY sp.season_id, sp.rank_by_avg_score, sp.full_name, gls.lock_signal
ORDER BY sp.season_id, sp.rank_by_avg_score, gls.lock_signal;

-- 3. Full row-level detail for manual eyeball review
WITH ranked_players AS (
    SELECT
        pss.player_id, pss.season_id, pss.full_name,
        ROW_NUMBER() OVER (
            PARTITION BY pss.season_id ORDER BY pss.avg_fantasy_score DESC, pss.player_id
        ) AS rank_by_avg_score
    FROM player_season_fantasy_stats pss
),
sample_players AS (
    SELECT player_id, season_id, full_name, rank_by_avg_score
    FROM ranked_players
    WHERE season_id IN ('22021', '22022', '22023', '22024', '22025')
      AND rank_by_avg_score IN (1, 11, 21, 31, 41, 51, 61, 71, 81, 91, 101, 111, 121, 131, 141)
)
SELECT
    sp.season_id, sp.rank_by_avg_score, sp.full_name,
    gls.game_date, gls.week_number, gls.games_remaining_in_week,
    gls.tier, gls.fantasy_score, gls.percentage_to_lock, gls.lock_signal
FROM sample_players sp
JOIN game_lock_signal gls
    ON gls.player_id = sp.player_id AND gls.season_id = sp.season_id
ORDER BY sp.season_id, sp.rank_by_avg_score, gls.game_date;
