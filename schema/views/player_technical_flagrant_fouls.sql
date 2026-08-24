-- Raw technical/flagrant foul counts per player per game, sourced from
-- PlayByPlayV3 (nba_api). Closes the long-documented, now precisely
-- quantified gap in game_fantasy_scores: this league's real Sleeper
-- scoring docks -2.0 for a technical foul (tf) and -2.0 for a flagrant
-- (ff), neither previously implemented since game_logs has no raw
-- column distinguishing these from ordinary personal fouls.
--
-- Every subType this could possibly need is hand-verified against real
-- Sleeper scores or the NBA's own rulebook, not assumed from label
-- text alone (8/23/26 investigation) -- see backfill_technical_
-- flagrant_fouls.py's own header for the full confirmed/rejected list.
-- Counts ONLY the subtypes confirmed to cost -2: 'Technical',
-- 'Flagrant Type 1', 'Flagrant Type 2', 'Hanging Technical', and both
-- players named in a 'Double Technical' event. Everything else
-- (Defense 3 Second, Delay Technical, Too Many Players Technical,
-- Double Personal, Flopping, and every ordinary foul subtype) is
-- deliberately excluded -- confirmed to cost nothing.
--
-- technical_flagrant_scan_log tracks which game_ids have already been
-- scanned, separate from the counts table itself -- most games have
-- ZERO technicals, so a game producing no rows in the counts table
-- would otherwise look identical to "never scanned" and get rescanned
-- every backfill run. Insert into the log after a game is processed,
-- regardless of whether it produced any foul rows.

CREATE TABLE IF NOT EXISTS player_technical_flagrant_fouls (
    player_id       INTEGER NOT NULL REFERENCES players(player_id),
    game_id         VARCHAR(20) NOT NULL,
    technical_fouls INTEGER NOT NULL DEFAULT 0,
    flagrant_fouls  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, game_id)
);

CREATE TABLE IF NOT EXISTS technical_flagrant_scan_log (
    game_id    VARCHAR(20) PRIMARY KEY,
    scanned_at TIMESTAMP NOT NULL DEFAULT now()
);

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM player_technical_flagrant_fouls;
SELECT COUNT(*) FROM technical_flagrant_scan_log;

-- Sanity check: no row should ever have both fields at 0 (there'd be
-- no reason to insert a row at all in that case)
SELECT COUNT(*) AS violations
FROM player_technical_flagrant_fouls
WHERE technical_fouls = 0 AND flagrant_fouls = 0;
