-- Raw Sleeper transactions (waivers, trades, free-agent drops), one
-- row per transaction. adds/drops map Sleeper player_id -> roster_id;
-- do not join to game_logs until the crosswalk table (step 2) exists.

DROP TABLE IF EXISTS sleeper_transactions;

CREATE TABLE sleeper_transactions (
    transaction_id  TEXT    PRIMARY KEY,
    league_id       TEXT    NOT NULL REFERENCES sleeper_leagues(league_id),
    type            TEXT,
    status          TEXT,
    week            INTEGER,
    roster_ids      INTEGER[],
    adds            JSONB,
    drops           JSONB,
    created         TIMESTAMP,
    synced_at       TIMESTAMP NOT NULL DEFAULT now()
);

CREATE INDEX idx_sleeper_transactions_league_week
ON sleeper_transactions (league_id, week);

-- =========================
-- Verification
-- =========================
SELECT league_id, type, COUNT(*) FROM sleeper_transactions GROUP BY league_id, type ORDER BY league_id, type;
