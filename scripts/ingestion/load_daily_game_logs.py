"""
Daily version of load_game_logs.py -- pulls only YESTERDAY's games
(default) via a date-scoped box score approach instead of looping every
rostered player's full season (that shape is right for a one-time
backfill, wrong for a nightly job -- see project notes on why). Uses
ScoreboardV2 to find the night's game_ids + home/visitor teams, then
BoxScoreTraditionalV2 per game -- a handful of API calls instead of
hundreds. Reuses game_logs.py's own load_game_logs() insert function so
the ON CONFLICT/composite-PK logic stays a single source of truth.

VERIFIED, NOT FIRST-PASS ANYMORE (8/10/26) -- BoxScoreTraditionalV2 was
confirmed dead for the 2025-26 season (no data published); switched to
V3, whose player/team-stats column shapes are confirmed against a real
pull. ScoreboardV2 has ALSO been migrated to V3 (get_scoreboard_games.py)
-- V3's shape turned out to have no direct home/away column at all,
derived instead from the game-leaders frame's leaderType field, confirmed
against a real pull and cross-checked two independent ways (see that
file's docstring). minutes format confirmed "MM:SS", matching
PlayerGameLog.

CHAINED (8/10/26): after loading game_logs, this now also calls
build_gap_reasons() scoped to the target date, AND
sync_game_fantasy_scores_weekly_effective() -- the latter is deliberately
NOT date-scoped (see that file's own docstring): it catches up ANY
game_logs rows missing a corresponding row here, regardless of source, so
a 2-way/bench player backfilled through backfill_missing_players.py or
backfill_single_player.py also gets picked up automatically, not just
tonight's games. team_schedule_b2b_flags (schema/rebuild_materialized_
views.sql's Step 1) is UNCHANGED and still needs a manual rebuild if the
schedule itself changes (postponement, makeup game) -- that's rare enough
not to chain in here.

DATE RANGE SUPPORT (added 8/21/26): a single day was the only unit this
could process -- no way to catch up several missing days at once (e.g.
forking this project mid-season, or coming back after time away).
run_for_date() now holds the actual per-day work, called once per day in
the requested range. The effective-table sync stays a single call AFTER
the whole range completes, not once per day -- it already catches up ANY
missing row regardless of date (per its own docstring), so calling it
mid-loop would just be redundant work N times instead of once. Endpoints
themselves were already date-scoped, not year-specific -- this only adds
the ability to loop over more than one date per invocation.

FIXED 8/21/26: sys.path.append() was placed AFTER the local module
imports that depend on it (data_cleaning_boxscore, get_scoreboard_games,
etc., all of which live one level up in flat scripts/, not alongside
this file in scripts/ingestion/) -- Python runs top to bottom, so those
imports were failing with ModuleNotFoundError before the path fix ever
ran. Moved to the top, right after importing sys/Path, before any local
import.

Run:
    python scripts/load_daily_game_logs.py                        (yesterday only, unchanged default)
    python scripts/load_daily_game_logs.py YYYY-MM-DD              (single day, unchanged)
    python scripts/load_daily_game_logs.py YYYY-MM-DD YYYY-MM-DD   (inclusive range, new)
"""

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nba_api.stats.endpoints import boxscoretraditionalv3
from data_cleaning_boxscore import clean_boxscore
from get_scoreboard_games import get_completed_games_with_home_away
from build_gap_reasons import build_gap_reasons
from sync_game_fantasy_scores_weekly_effective import sync_game_fantasy_scores_weekly_effective
from load_game_logs import load_game_logs, GAME_LOGS_COLUMNS
from db_connection import get_connection


def season_for_date(d: date) -> str:
    """'2026-02-14' -> '22025'. Same Oct-cutoff heuristic as
    load_daily_team_schedule.py -- confirm near season boundaries."""
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"2{start_year}"  # matches this project's season_id format, e.g. '22024'


def daterange(start_date: date, end_date: date):
    """Inclusive date range, start_date..end_date -- yields one date() per day."""
    for n in range((end_date - start_date).days + 1):
        yield start_date + timedelta(days=n)


def run_for_date(target_date: date) -> int:
    """All the per-day work: find that night's games, load box scores
    (with per-game rollback so one bad insert can't poison the rest of
    the night -- see the try/except below), and annotate that date's
    gaps. Returns total rows inserted for this date. The effective-table
    sync is NOT called here -- see module docstring for why."""
    season_id = season_for_date(target_date)

    print(f"Finding games for {target_date.isoformat()}...")
    games = get_completed_games_with_home_away(target_date.isoformat())
    print(f"{len(games)} completed game(s) found.")

    conn = get_connection()
    total_inserted = 0
    failures = []

    for i, g in enumerate(games, start=1):
        try:
            box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=g["game_id"])
            dfs = box.get_data_frames()
            player_stats, team_stats = dfs[0], dfs[2]  # confirmed frame order, 8/10/26

            cleaned = clean_boxscore(
                player_stats, team_stats, g["game_id"], target_date.isoformat(), season_id,
                g["home_team_id"], g["visitor_team_id"],
            )[GAME_LOGS_COLUMNS]

            n = load_game_logs(cleaned, conn)
            total_inserted += n
            print(f"[{i}/{len(games)}] game_id {g['game_id']}: {n} rows")
        except Exception as e:
            print(f"[{i}/{len(games)}] game_id {g['game_id']}: FAILED — {e}")
            failures.append((g["game_id"], str(e)))
            # CRITICAL (found 8/10/26): without this, one failed insert
            # poisons the WHOLE transaction -- every subsequent game in
            # this run fails too ("current transaction is aborted"),
            # even though their data was fine. One bad stat line could
            # silently cost an entire night's data without this.
            conn.rollback()

    conn.close()

    print(f"Inserted {total_inserted} row(s) for {target_date.isoformat()}.")
    if failures:
        print(f"{len(failures)} game(s) failed:")
        for gid, err in failures:
            print(f"  - {gid}: {err}")

    print(f"Annotating any new gaps from {target_date.isoformat()} with injury reasons...")
    gap_conn = get_connection()
    try:
        # date_to = date_from: scopes to EXACTLY this one day, not an
        # open-ended range. Found the hard way tonight -- without date_to,
        # this silently processed every gap through the rest of the
        # season instead of just this date's games.
        build_gap_reasons(gap_conn, date_from=target_date.isoformat(), date_to=target_date.isoformat())
    finally:
        gap_conn.close()

    return total_inserted


def main():
    args = sys.argv[1:]
    if len(args) == 2:
        start_date = date.fromisoformat(args[0])
        end_date = date.fromisoformat(args[1])
    elif len(args) == 1:
        start_date = end_date = date.fromisoformat(args[0])
    else:
        start_date = end_date = date.today() - timedelta(days=1)

    if start_date > end_date:
        raise ValueError(f"start date {start_date} is after end date {end_date}")

    grand_total = 0
    for target_date in daterange(start_date, end_date):
        print(f"\n{'=' * 60}")
        print(f"Processing {target_date.isoformat()}")
        print(f"{'=' * 60}")
        grand_total += run_for_date(target_date)

    print(f"\n{'=' * 60}")
    print(f"Done. {grand_total} total row(s) inserted across "
          f"{(end_date - start_date).days + 1} day(s).")

    print("\nSyncing game_fantasy_scores_weekly_effective once for the whole run")
    print("(catches up ANY missing rows, not just this range -- see file docstring)...")
    sync_conn = get_connection()
    try:
        n_synced = sync_game_fantasy_scores_weekly_effective(sync_conn)
        print(f"Synced {n_synced} new row(s).")
    finally:
        sync_conn.close()


if __name__ == "__main__":
    main()
