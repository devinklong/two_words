DROP TABLE IF EXISTS gap_reasons;

CREATE TABLE gap_reasons (
    player_id    INTEGER      NOT NULL REFERENCES players(player_id),
    team_id      INTEGER      NOT NULL REFERENCES teams(team_id),
    game_id      VARCHAR(20)  NOT NULL,
    game_date    DATE         NOT NULL,
    status       VARCHAR(50),   -- e.g. 'Out', 'Questionable', NULL if not on report
    reason       VARCHAR(200),  -- e.g. 'Injury/Illness - Right Knee; Soreness', NULL if not on report
    is_explained BOOLEAN      NOT NULL,  -- TRUE if found on injury report, FALSE if likely coach's decision
    PRIMARY KEY (player_id, game_id)
);

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) AS total_gap_reasons FROM gap_reasons;

SELECT is_explained, COUNT(*) FROM gap_reasons GROUP BY is_explained;

-- Sanity check against the notebook's manual validation — all 8 of
-- Raynaud's gaps came back "NOT on injury report (likely coach's decision)"
SELECT gr.*
FROM gap_reasons gr
JOIN players p ON p.player_id = gr.player_id
WHERE p.full_name ILIKE '%raynaud%'
ORDER BY gr.game_date;

TRUNCATE TABLE gap_reasons;