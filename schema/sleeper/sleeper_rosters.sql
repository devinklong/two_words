-- Raw Sleeper rosters, one row per roster per league (one row per
-- fantasy team per season). players/starters are Sleeper's own
-- player_id scheme, not this project's player identity -- do not
-- join to game_logs until the crosswalk table (step 2) exists.

DROP TABLE IF EXISTS sleeper_rosters;

CREATE TABLE sleeper_rosters (
    league_id   TEXT    NOT NULL REFERENCES sleeper_leagues(league_id),
    roster_id   INTEGER NOT NULL,
    owner_id    TEXT,
    players     TEXT[],
    starters    TEXT[],
    settings    JSONB,
    synced_at   TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, roster_id)
);

-- =========================
-- Verification
-- =========================
SELECT league_id, COUNT(*) AS n_rosters FROM sleeper_rosters GROUP BY league_id;
-- Should match total_rosters from sleeper_leagues for each league_id.
