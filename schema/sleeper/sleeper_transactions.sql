-- Raw Sleeper transactions (waivers, trades, free-agent drops), one
-- row per transaction. adds/drops map Sleeper player_id -> roster_id;
-- do not join to game_logs until the crosswalk table (step 2) exists.
--
-- Changed 10/21/26: additive and safe to re-run. It used to start with
-- DROP TABLE IF EXISTS, which would delete every stored transaction on a
-- rerun. Also adds waiver_bid (the FAAB bid, from the transaction's
-- settings.waiver_bid; NULL for non-waiver transactions). To reset the
-- table deliberately, drop it by hand.

CREATE TABLE IF NOT EXISTS sleeper_transactions (
    transaction_id  TEXT    PRIMARY KEY,
    league_id       TEXT    NOT NULL REFERENCES sleeper_leagues(league_id),
    type            TEXT,
    status          TEXT,
    week            INTEGER,
    roster_ids      INTEGER[],
    adds            JSONB,
    drops           JSONB,
    created         TIMESTAMP,
    synced_at       TIMESTAMP NOT NULL DEFAULT now(),
    waiver_bid      INTEGER
);

-- For databases created before waiver_bid existed (no-op otherwise).
ALTER TABLE sleeper_transactions ADD COLUMN IF NOT EXISTS waiver_bid INTEGER;

CREATE INDEX IF NOT EXISTS idx_sleeper_transactions_league_week
ON sleeper_transactions (league_id, week);

-- =========================
-- Verification
-- =========================
SELECT league_id, type, COUNT(*) FROM sleeper_transactions GROUP BY league_id, type ORDER BY league_id, type;
