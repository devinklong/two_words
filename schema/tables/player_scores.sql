-- schema/tables/player_scores.sql
--
-- Real, locked fantasy score per (roster, week, player) -- the
-- player-level counterpart to sleeper_matchup_points_snapshots
-- (team_scores). Never previously wired into a table: v3.1 built
-- verify_player_scores_against_xlsx.py to validate the xlsx against
-- game_fantasy_scores, but the xlsx itself -- the manually-verified
-- ground truth of which specific game got locked -- was never loaded
-- into the database. This is that missing ingestion target.
--
-- Keyed by (league_id, week, roster_id, sleeper_player_id) -- matching
-- sleeper_matchup_points_snapshots' key shape exactly (league_id/week,
-- not season_id/week_number), so this joins directly against
-- sleeper_matchups (also league_id/week/roster_id) for the team-side
-- slot-value analysis this exists to support, with no season_id
-- translation step needed.
--
-- Grain: one row per roster per week per locked player -- up to 9 rows
-- per roster per week (one per starting slot), matching the real
-- Lock-In Mode design (each starting slot locks ONE game
-- independently; team weekly total = SUM of all starters' locked
-- scores).
--
-- BYE/NULL sentinel rows (bracket-eliminated roster/weeks) are
-- deliberately NOT stored here -- same precedent as team_scores
-- (backfill_manual_team_points.py skips BYE rows entirely, since
-- sleeper_matchups.matchup_id IS NULL already represents "no
-- opponent"). A recorded points=0 IS stored -- that's a real DNP
-- outcome after being locked, not a sentinel (see
-- verify_player_scores_against_xlsx.py's docstring).
--
-- Plain upsert, NOT an append-only change log like
-- sleeper_matchup_points_snapshots: that table's append-only design
-- exists specifically to preserve history against Sleeper's own
-- unreliable live sync overwriting values. This table has no live-sync
-- counterpart at all -- it's a one-time manual backfill from an
-- already-validated xlsx -- so a plain upsert is the correct, simpler
-- choice, not a shortcut.

CREATE TABLE IF NOT EXISTS player_scores (
    league_id           TEXT    NOT NULL REFERENCES sleeper_leagues(league_id),
    week                INTEGER NOT NULL,
    roster_id           INTEGER NOT NULL,
    sleeper_player_id   TEXT    NOT NULL,
    points              NUMERIC NOT NULL,
    synced_at           TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, week, roster_id, sleeper_player_id)
);

CREATE INDEX IF NOT EXISTS idx_player_scores_league_week
ON player_scores (league_id, week);
