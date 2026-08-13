-- Raw Sleeper league members, one row per user per league. metadata
-- holds Sleeper's free-form team name/avatar fields as JSONB since
-- their presence varies by user.

DROP TABLE IF EXISTS sleeper_users;

CREATE TABLE sleeper_users (
    league_id     TEXT    NOT NULL REFERENCES sleeper_leagues(league_id),
    user_id       TEXT    NOT NULL,
    display_name  TEXT,
    is_owner      BOOLEAN,
    metadata      JSONB,
    synced_at     TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, user_id)
);

-- =========================
-- Verification
-- =========================
SELECT league_id, COUNT(*) AS n_users FROM sleeper_users GROUP BY league_id;
