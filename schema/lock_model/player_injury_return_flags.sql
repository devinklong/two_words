-- Persists which specific games are a player's return from an
-- injury-explained absence, as a real view -- defined once and reused
-- wherever a return-game flag is needed, same reasoning as
-- player_tiers.sql.
--
-- Flags ONLY the single return game (games_since_return = 1 in
-- injury_return_analysis.sql's terms), not a multi-game window.
-- Confirmed via injury_return_analysis.sql (8/10/26) that the dip is
-- sharply front-loaded on this specific game and washes out by game 3
-- -- for high-usage players specifically (the ownable pool this tool
-- targets), return_game_only showed a -3.11 pt dip vs baseline (n=7,685)
-- while return_plus_2_games had already faded to -2.17, so a single-game
-- flag matches the empirical shape better than a window.
--
-- ASSUMPTION (confirm/adjust): gap_reasons.reason IS NOT NULL marks an
-- injury-explained gap -- same assumption as injury_return_analysis.sql.
-- If that predicate is wrong there, it's wrong here too; fix both files
-- together.

DROP VIEW IF EXISTS player_injury_return_flags CASCADE;

CREATE VIEW player_injury_return_flags AS
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
)
SELECT DISTINCT
    player_id,
    team_id,
    candidate_return_date AS game_date,
    TRUE AS is_return_game
FROM next_game_after_gap nga
WHERE NOT EXISTS (
    -- collapses a multi-game absence to one true return game, not one
    -- row per missed game in the absence -- same logic as
    -- injury_return_analysis.sql's return_games CTE
    SELECT 1 FROM injury_gaps ig2
    WHERE ig2.player_id = nga.player_id AND ig2.team_id = nga.team_id
      AND ig2.gap_date > nga.gap_date AND ig2.gap_date < nga.candidate_return_date
);

-- =========================
-- Verification
-- =========================

-- Should roughly match injury_return_analysis.sql's return_game_only
-- game_count (29,867 full population), modulo any return games that
-- fall outside game_fantasy_scores_weekly's 24-week fantasy window
SELECT COUNT(*) AS total_return_games FROM player_injury_return_flags;
