-- Raw Sleeper league settings, one row per season (league_id changes
-- each season; previous_league_id chains back to the prior year).
-- scoring_settings and roster_positions stored as JSONB/array since
-- their shape is league-config-dependent, not fixed columns -- derived
-- views built later once real shapes are confirmed.

DROP TABLE IF EXISTS sleeper_leagues;

CREATE TABLE sleeper_leagues (
    league_id           TEXT    PRIMARY KEY,
    previous_league_id  TEXT,
    season              TEXT    NOT NULL,
    name                TEXT,
    status              TEXT,
    total_rosters       INTEGER,
    roster_positions    TEXT[],
    scoring_settings    JSONB,
    settings            JSONB,
    synced_at           TIMESTAMP NOT NULL DEFAULT now()
);

-- =========================
-- Verification
-- =========================
SELECT league_id, season, previous_league_id, name FROM sleeper_leagues ORDER BY season;
-- Expect exactly 2 rows (2024, 2025), with the 2025 row's previous_league_id equal to the 2024 row's league_id.
