-- Raw Sleeper matchups, one row per roster per week. matchup_id links
-- the two rosters facing each other that week (same matchup_id = same
-- fantasy game) -- this is the FANTASY opponent, distinct from the NBA
-- opponent_team_id tracked elsewhere in this project.

DROP TABLE IF EXISTS sleeper_matchups;

CREATE TABLE sleeper_matchups (
    league_id       TEXT    NOT NULL REFERENCES sleeper_leagues(league_id),
    week            INTEGER NOT NULL,
    roster_id       INTEGER NOT NULL,
    matchup_id      INTEGER,
    points          NUMERIC,
    players         TEXT[],
    starters        TEXT[],
    players_points  JSONB,
    synced_at       TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, week, roster_id)
);

-- =========================
-- Verification
-- =========================
SELECT league_id, week, matchup_id, COUNT(*) AS n_rosters
FROM sleeper_matchups
WHERE matchup_id IS NOT NULL
GROUP BY league_id, week, matchup_id
HAVING COUNT(*) != 2;
-- Expect 0 rows among matchups that actually have a matchup_id. Rows with
-- matchup_id = NULL are expected in playoff weeks (bracket-eliminated
-- teams still get a roster row with no active matchup that week) --
-- not a sync failure.
