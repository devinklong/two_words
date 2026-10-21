# Daily runbook (live season)

Written 10/20/26, the 2026-27 season opener. Run from the project root.
`scripts/run_daily.py` does steps 2-11 in one command; this doc is the
manual version with checkpoints, for the first run(s) and for debugging.

## Why the order matters

- `game_logs.player_id` has a FK to `nba_players` again (restored 10/20/26).
  A new player missing from `nba_players` makes the whole game-log insert
  fail, so the players load comes BEFORE the game-log load.
- `sync_game_fantasy_scores_weekly_effective` only INSERTS missing rows and
  never updates ones it already wrote. It reads `team_schedule_b2b_flags`
  (a materialized view) for `effective_games_remaining_in_week`. If the
  schedule and that matview are stale when it runs, those rows are wrong
  permanently. So: schedule -> refresh matview -> game logs.
- `team_schedule` only receives PLAYED games from the daily loader, so
  upcoming games come from `load_season_schedule.py`; without them the
  "games remaining" values are wrong at insert time and stay wrong.
- `fantasy_weeks` for 22026 was seeded by hand on 10/20 (week 1 starts
  Mon 2026-10-19). `team_schedule` is built from games, so the real check
  is possible only after the opener lands.

## First morning (Wed 10/21): manual, with checkpoints

1. Backup
   `mkdir -p ~/backups && pg_dump postgres > ~/backups/postgres_$(date +%F)_pre_load.sql && ls -lh ~/backups`

2. Schedules (the live source of "games remaining")
   `python scripts/ingestion/load_season_schedule.py --write`
   `python scripts/ingestion/load_daily_team_schedule.py`
   The daily loader only ever returns games ALREADY PLAYED (verified
   10/21/26: a future date range inserted 0 rows). The season loader pulls
   the full regular-season schedule from nba_api ScheduleLeagueV2 and
   inserts upcoming games with NULL results. It refuses to write unless it
   finds 1230 games and 82 per team. Idempotent; safe to rerun daily
   (picks up postponements for unplayed games only).
   CHECK: 22026 now has 2460 rows and runs through April:
   `psql -d postgres -c "SELECT season_id, MIN(game_date), MAX(game_date), COUNT(*), COUNT(wl) AS played FROM team_schedule GROUP BY 1 ORDER BY 1"`
   If the loader's column auto-detection fails it prints the columns and
   exits without writing; run `--inspect` and send the output.

3. Refresh the b2b matview
   `psql -d postgres -c "REFRESH MATERIALIZED VIEW team_schedule_b2b_flags"`

4. fantasy_weeks (additive, no-op for 2026)
   `psql -d postgres -f schema/tables/fantasy_weeks.sql`
   CHECK: first game's Monday equals the seeded week 1 start:
   `psql -d postgres -c "SELECT MIN(game_date) FROM team_schedule WHERE season_id = '22026'"` should be 2026-10-20 (Mon 10/19 starts week 1).
   If it is EARLIER than 10/20, delete and reseed 22026 (the seed would be wrong).

5. Players (offline, safe to rerun)
   `PYTHONPATH=scripts python scripts/ingestion/load_players.py`

6. Game logs for 10/20 (chains the weekly-effective sync and injuries)
   `python scripts/ingestion/load_daily_game_logs.py 2026-10-20`
   If it fails on a foreign key naming a player_id: that rookie isn't in
   `nba_players`. Check `sed -n 1,30p scripts/ingestion/backfill_single_player.py`
   for its arguments, backfill that player, then rerun this step.
   CHECKS:
   `psql -d postgres -c "SELECT COUNT(DISTINCT game_id), COUNT(*) FROM game_logs WHERE season_id = '22026'"`
   `psql -d postgres -c "SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective WHERE season_id = '22026'"`
   `psql -d postgres -c "SELECT COUNT(*) FROM gap_reasons WHERE game_date >= '2026-10-20'"` (injuries)
   Skim `cleaning_logs/` for new ambiguous-name files from build_gap_reasons.

7. Players missing from game_logs (usually a no-op)
   `python scripts/ingestion/backfill_missing_players.py`
   CHECK: `psql -d postgres -c "SELECT COUNT(DISTINCT player_id) FROM game_logs WHERE player_id NOT IN (SELECT player_id FROM nba_players)"` = 0

8. Sleeper crosswalk (rookies who have now played), then sync
   `python scripts/sleeper/build_sleeper_player_crosswalk.py`
   `python scripts/sleeper/run_daily_sync.py`
   Any rookie still "Unmatched" (e.g. a name-format mismatch like "AJ" vs
   "A.J.") needs a manual crosswalk row with match_method='manual' (see
   cleaning_logs/). Brandon Williams (sleeper 2397) is ambiguous: 1585 vs
   1630314, only matters if rostered.

9. Independent verification
   `python scripts/verify_current_league_independently.py --week 1`

10. Decision tools (each should run and show sensible output)
   a. Lock signal on 2026 games and which baseline it used:
      `psql -d postgres -c "SELECT player_stats_source, lock_signal, COUNT(*) FROM game_lock_signal WHERE season_id = '22026' GROUP BY 1,2 ORDER BY 1,2"`
      Expect a fallback (prior-season) source, not zero rows. Zero rows
      means the bootstrap isn't covering week 1: stop and ask.
   b. Lock calculator on a real 10/20 game (pick a player/game_id from game_logs):
      `python scripts/lock_decision_input.py PLAYER_ID --game-id GAME_ID --season-id 22026 --game-date 2026-10-20`
   c. Waiver wire:
      `python scripts/waiver_wire_finder.py --season-id 22026 --limit 10`
      Rookies won't appear: player_tiers needs 20 games, and the one-year
      fallback has no history for them. That is expected.
   d. Matchup analysis (your roster_id is 8):
      `python scripts/opponent_scout.py 8 --week 1 --season-id 22026`  (8 = sweetdiddlydee; also set as MY_ROSTER_ID in scripts/run_daily.py)
      Unmatched rookies show "no crosswalk match" until step 8 resolves them.

11. Preflight, then commit
   `python scripts/season_opener_preflight.py --week 1`
   Expected now: game_logs/weekly-effective rows > 0, fantasy_weeks = 24.
   Still-expected WARN: rostered rookies without an nba_player_id.

## After it works: every morning

`python scripts/run_daily.py --dry-run` (read the plan), then `python scripts/run_daily.py`.
It loads yesterday's games, stops at the first failure, writes
`scripts/logs/daily_<date>.log`, and ends with the verifier and preflight.
Run it AFTER the last West Coast game has finished (morning is fine).

## Do NOT run casually

- `scripts/rebuild_lock_pipeline.py` has no --help: it just runs the whole
  rebuild, DROPs with CASCADE included. After 2026 data enters the database
  its final regression check (LOCK/HOLD/PASS 52.7/24.3/23.0, Jokic
  lock_bar 79.465) will legitimately drift; that is a re-baseline, not a bug.
- Any `.sql` file that starts with `DROP ... CASCADE` (e.g. the
  sleeper_current_league section of roster_ownership_view.sql and the
  game_lock_signal / ownable_player_pool files). A CASCADE silently drops
  everything built on top of the object.
- `models/percentage_to_lock.sql` wipes `hold_value_curve_params_by_tier`;
  rerun `scripts/fit_hold_value_curve_by_tier.py` after it.

## Weekly / occasional

- Weekly: spot-check Sleeper points against the app once real scores exist.
- Open question: how `player_scores` gets live 2026 data (the xlsx was a
  one-time load; `locked_scores_by_slot` has no 2026 rows until it does).
- Watch the 20-game bootstrap flip and the one-year fallback as the season
  progresses.
- Refit hold curves (`fit_hold_value_curve_by_tier.py`) only deliberately.
- Season end: re-verify snapshots, back up.

## Doc / repo cleanup still owed (from 10/20/26)

- Rename `players` -> `nba_players` in docs/schema.md and
  docs/schema_relationship_diagram.md; add a "naming rule" note: anything
  generic gets an `nba_` or `eredivisie_` prefix until the projects are
  split into separate schemas/databases.
- Add the shared-database collision to docs/architecture_risks.md.
- Remove CASCADE from view files; fix stale `players.full_name` joins in
  roster_ownership_view.sql and transactions_view.sql.
- Rename `teams` -> `nba_teams`.
- Refresh the league settings row (`sleeper_leagues.synced_at` is stale).
- Update README/methodology_notes daily-run order to point here.
