-- nba_lock_decisions: which player/game you locked into which lineup slot, per fantasy week (added 10/23/26).
-- Written by scripts/record_lock.py. Read by scripts/my_lock_report.py to know which slots are already used.
-- Each row also freezes the model's read at the time (bar, signal, tier, games left), so later analysis can
-- compare what the model said with what you did and with what happened (join game_fantasy_scores_weekly_effective
-- for the scores of the games that came after). One row per slot per week; UTIL is stored as UTIL1 / UTIL2.
-- Additive and idempotent.
CREATE TABLE IF NOT EXISTS nba_lock_decisions (
    league_id          TEXT        NOT NULL,
    roster_id          INTEGER     NOT NULL,
    season_id          TEXT        NOT NULL,
    week_number        INTEGER     NOT NULL,
    slot               TEXT        NOT NULL CHECK (slot IN ('PG','SG','G','SF','PF','F','C','UTIL1','UTIL2')),
    sleeper_player_id  TEXT,
    nba_player_id      INTEGER,
    player_name        TEXT        NOT NULL,
    game_id            TEXT        NOT NULL,
    game_date          DATE        NOT NULL,
    fantasy_score      NUMERIC     NOT NULL,
    lock_bar           NUMERIC,
    model_signal       TEXT,
    model_percentage_to_lock NUMERIC,
    tier               TEXT,
    stats_source       TEXT,
    games_left_effective NUMERIC,
    source             TEXT        NOT NULL DEFAULT 'manual',
    recorded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, roster_id, week_number, slot)
);
