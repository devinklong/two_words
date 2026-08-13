-- One-time fix: sleeper_matchups was built including players_points and
-- points (Sleeper's own computed fantasy totals), violating the
-- project's own data-source-split rule (Sleeper = league structure only,
-- never scoring). Drops just those two columns -- players/starters/
-- roster membership data is untouched, no need to resync.

ALTER TABLE sleeper_matchups DROP COLUMN IF EXISTS players_points;
ALTER TABLE sleeper_matchups DROP COLUMN IF EXISTS points;

-- =========================
-- Verification
-- =========================
SELECT column_name FROM information_schema.columns WHERE table_name = 'sleeper_matchups';
-- Expect: league_id, week, roster_id, matchup_id, players, starters, synced_at -- nothing else.
