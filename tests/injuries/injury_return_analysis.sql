-- Diagnostic: does performance dip on return from an injury-explained
-- absence, and if so, does the effect show up only in the immediate
-- return game or linger for a couple games after? Tests BOTH windows
-- independently rather than assuming one -- same approach as
-- b2b_analysis.sql's first-night/second-night split.
--
-- ASSUMPTION (confirm/adjust): gap_reasons.reason IS NOT NULL marks an
-- injury-explained gap. Swap this predicate if the real column differs.
--
-- BUG FIX #1 (8/10/26): the second (high-usage-only) query originally
-- referenced `capped` from the first query's WITH chain. CTEs don't
-- persist across separate statements once a semicolon ends the first
-- one -- caused "relation capped does not exist". Fixed by giving the
-- second query its own full WITH chain, same pattern as
-- weekly_outcome_simulation.sql's train/validate split.
--
-- BUG FIX #2 (8/10/26) -- SIGNIFICANT, invalidates earlier numbers:
-- post_return_games never selected season_id from game_fantasy_scores,
-- and the final join to player_season_fantasy_stats matched on
-- player_id ONLY. player_season_fantasy_stats has one row per player
-- PER SEASON (up to 5 for a player active all 5 years) -- so every
-- return game fanned out against every season-row for that player,
-- inflating row counts (~29,867 reported vs the true 7,669 distinct
-- return games confirmed via player_injury_return_flags.sql) AND
-- averaging in baseline scores from the WRONG seasons. The earlier
-- reported deltas (-1.04 full population, -3.11 high-usage) were
-- computed on this corrupted join and should be treated as unreliable
-- until rerun with this fix. Fixed by carrying season_id through
-- post_return_games/capped and joining pss on (player_id, season_id).

WITH injury_gaps AS (
    SELECT player_id, team_id, game_date AS gap_date
    FROM gap_reasons
    WHERE reason IS NOT NULL  -- <-- adjust if this isn't the right flag
),
next_game_after_gap AS (
    SELECT ig.player_id, ig.team_id, ig.gap_date,
        MIN(gl.game_date) AS candidate_return_date
    FROM injury_gaps ig
    JOIN game_logs gl
        ON gl.player_id = ig.player_id AND gl.team_id = ig.team_id
        AND gl.game_date > ig.gap_date
    GROUP BY ig.player_id, ig.team_id, ig.gap_date
),
return_games AS (
    SELECT DISTINCT player_id, team_id, candidate_return_date AS return_date
    FROM next_game_after_gap nga
    WHERE NOT EXISTS (
        SELECT 1 FROM injury_gaps ig2
        WHERE ig2.player_id = nga.player_id AND ig2.team_id = nga.team_id
          AND ig2.gap_date > nga.gap_date AND ig2.gap_date < nga.candidate_return_date
    )
),
post_return_games AS (
    SELECT
        rg.player_id, rg.team_id, rg.return_date,
        gfs.season_id, gfs.game_date, gfs.fantasy_score, gfs.minutes,
        ROW_NUMBER() OVER (
            PARTITION BY rg.player_id, rg.team_id, rg.return_date
            ORDER BY gfs.game_date
        ) AS games_since_return
    FROM return_games rg
    JOIN game_fantasy_scores gfs
        ON gfs.player_id = rg.player_id AND gfs.team_id = rg.team_id
        AND gfs.game_date >= rg.return_date
    WHERE gfs.game_date <= rg.return_date + INTERVAL '10 days'
),
capped AS (
    SELECT * FROM post_return_games WHERE games_since_return <= 3
)
SELECT
    CASE WHEN c.games_since_return = 1 THEN 'return_game_only'
         ELSE 'return_plus_2_games' END AS window_label,
    COUNT(*) AS game_count,
    ROUND(AVG(c.fantasy_score), 2) AS avg_fantasy_score,
    ROUND(AVG(c.minutes), 2) AS avg_minutes,
    ROUND(AVG(pss.avg_fantasy_score), 2) AS player_season_baseline_score,
    ROUND(AVG(c.fantasy_score - pss.avg_fantasy_score), 2) AS avg_delta_vs_baseline
FROM capped c
JOIN player_season_fantasy_stats pss
    ON pss.player_id = c.player_id AND pss.season_id = c.season_id
GROUP BY CASE WHEN c.games_since_return = 1 THEN 'return_game_only' ELSE 'return_plus_2_games' END;

-- Same restricted to high-usage players, like the B2B check -- avoids
-- bench-player noise diluting the signal. Own full WITH chain (see bug
-- fix #1 above) since CTEs don't carry across the semicolon boundary.
WITH injury_gaps AS (
    SELECT player_id, team_id, game_date AS gap_date
    FROM gap_reasons
    WHERE reason IS NOT NULL  -- <-- adjust if this isn't the right flag
),
next_game_after_gap AS (
    SELECT ig.player_id, ig.team_id, ig.gap_date,
        MIN(gl.game_date) AS candidate_return_date
    FROM injury_gaps ig
    JOIN game_logs gl
        ON gl.player_id = ig.player_id AND gl.team_id = ig.team_id
        AND gl.game_date > ig.gap_date
    GROUP BY ig.player_id, ig.team_id, ig.gap_date
),
return_games AS (
    SELECT DISTINCT player_id, team_id, candidate_return_date AS return_date
    FROM next_game_after_gap nga
    WHERE NOT EXISTS (
        SELECT 1 FROM injury_gaps ig2
        WHERE ig2.player_id = nga.player_id AND ig2.team_id = nga.team_id
          AND ig2.gap_date > nga.gap_date AND ig2.gap_date < nga.candidate_return_date
    )
),
post_return_games AS (
    SELECT
        rg.player_id, rg.team_id, rg.return_date,
        gfs.season_id, gfs.game_date, gfs.fantasy_score, gfs.minutes,
        ROW_NUMBER() OVER (
            PARTITION BY rg.player_id, rg.team_id, rg.return_date
            ORDER BY gfs.game_date
        ) AS games_since_return
    FROM return_games rg
    JOIN game_fantasy_scores gfs
        ON gfs.player_id = rg.player_id AND gfs.team_id = rg.team_id
        AND gfs.game_date >= rg.return_date
    WHERE gfs.game_date <= rg.return_date + INTERVAL '10 days'
),
capped AS (
    SELECT * FROM post_return_games WHERE games_since_return <= 3
)
SELECT
    CASE WHEN c.games_since_return = 1 THEN 'return_game_only'
         ELSE 'return_plus_2_games' END AS window_label,
    COUNT(*) AS game_count,
    ROUND(AVG(c.fantasy_score), 2) AS avg_fantasy_score,
    ROUND(AVG(c.minutes), 2) AS avg_minutes,
    ROUND(AVG(c.fantasy_score - pss.avg_fantasy_score), 2) AS avg_delta_vs_baseline
FROM capped c
JOIN player_season_fantasy_stats pss
    ON pss.player_id = c.player_id AND pss.season_id = c.season_id
WHERE pss.avg_fantasy_score >= 30
GROUP BY CASE WHEN c.games_since_return = 1 THEN 'return_game_only' ELSE 'return_plus_2_games' END;
