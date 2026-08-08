-- team_schedule_b2b_flags was a plain view using LAG/LEAD window functions.
-- Every correlated subquery that touched it (in
-- game_fantasy_scores_weekly_effective) forced Postgres to recompute those
-- window calculations from scratch, since window functions can't use an
-- index and a plain view has no stored result to reuse. Materializing it
-- computes the B2B flags ONCE and stores them as real rows, which the
-- index below then makes fast to filter against.
--
-- Since team_schedule is static historical data (not changing during a
-- session), this doesn't need auto-refresh logic — just re-run
-- REFRESH MATERIALIZED VIEW manually after any future team_schedule load
-- (e.g. when a new season gets backfilled next year).

DROP VIEW IF EXISTS team_schedule_b2b_flags CASCADE;
CREATE MATERIALIZED VIEW team_schedule_b2b_flags AS
SELECT
    ts.*,
    (ts.game_date - LAG(ts.game_date) OVER (
        PARTITION BY ts.team_id, ts.season_id ORDER BY ts.game_date
    )) = 1 AS is_second_night_of_b2b,
    (LEAD(ts.game_date) OVER (
        PARTITION BY ts.team_id, ts.season_id ORDER BY ts.game_date
    ) - ts.game_date) = 1 AS is_first_night_of_b2b
FROM team_schedule ts;

CREATE INDEX idx_b2b_flags_team_season_date
ON team_schedule_b2b_flags (team_id, season_id, game_date);

-- The DROP ... CASCADE above will have dropped
-- game_fantasy_scores_weekly_effective too, since it depends on this view.
-- Rerun that file after this one to recreate it against the materialized
-- version.

-- =========================
-- Verification
-- =========================

SELECT COUNT(*) FROM team_schedule_b2b_flags;
SELECT COUNT(*) FROM team_schedule;  -- should match exactly

-- Confirm the flags themselves are unchanged from before materializing
SELECT
    SUM(is_second_night_of_b2b::INT) AS total_b2b_second_nights,
    SUM(is_first_night_of_b2b::INT) AS total_b2b_first_nights
FROM team_schedule_b2b_flags;
