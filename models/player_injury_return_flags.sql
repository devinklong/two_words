-- Flags a player's single return game after an injury-explained absence
-- (gap_reasons.reason IS NOT NULL) -- flags only THAT game, not a window,
-- since the effect was found to be sharply front-loaded and fade by game 3.
-- Currently exposed as data only in game_lock_signal (not used in the
-- decision) after a tested lock_bar penalty was found net-negative.

DROP VIEW IF EXISTS player_injury_return_flags CASCADE;

CREATE VIEW player_injury_return_flags AS
WITH injury_gaps AS (
    SELECT player_id, team_id, game_date AS gap_date
    FROM gap_reasons
    WHERE reason IS NOT NULL  -- adjust if this isn't the right injury-vs-coach's-decision flag
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
-- collapses a multi-game absence to one true return game, not one row per missed game
WHERE NOT EXISTS (
    SELECT 1 FROM injury_gaps ig2
    WHERE ig2.player_id = nga.player_id AND ig2.team_id = nga.team_id
      AND ig2.gap_date > nga.gap_date AND ig2.gap_date < nga.candidate_return_date
);

-- Should land around 7,669 (confirmed 8/10/26)
SELECT COUNT(*) AS total_return_games FROM player_injury_return_flags;
