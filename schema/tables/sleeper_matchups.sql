-- Raw Sleeper matchups, one row per roster per week -- STRUCTURE ONLY.
-- players_points and points (Sleeper's own computed fantasy totals) are
-- deliberately excluded: this project computes fantasy_score itself
-- (game_fantasy_scores_weekly_effective), and never trusts a second,
-- unverified scoring engine for the same numbers. Sleeper is a source
-- for WHO played on WHICH roster WHEN, never for HOW MANY POINTS.

DROP TABLE IF EXISTS sleeper_matchups;

CREATE TABLE sleeper_matchups (
    league_id   TEXT    NOT NULL REFERENCES sleeper_leagues(league_id),
    week        INTEGER NOT NULL,
    roster_id   INTEGER NOT NULL,
    matchup_id  INTEGER,
    players     TEXT[],
    starters    TEXT[],
    synced_at   TIMESTAMP NOT NULL DEFAULT now(),
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
-- Expect 0 rows among matchups that have a matchup_id. matchup_id = NULL
-- rows are expected in playoff weeks (bracket-eliminated teams).
