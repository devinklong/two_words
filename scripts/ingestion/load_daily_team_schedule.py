"""
Daily version of load_team_schedule.py -- pulls only YESTERDAY's games
(default) for all 30 teams instead of a full season, using
get_team_schedule()'s new date_from/date_to params. Reuses
clean_team_schedule() and load_team_schedule() unchanged, so the insert
logic (ON CONFLICT DO NOTHING, composite PK) stays a single source of
truth with the full-backfill script.

Season is still a required nba_api param even when date-filtering --
season_for_date() below is a simple Oct-cutoff heuristic (NBA season
"N-N+1" runs Oct through ~June) -- fine for in-season daily runs, but
confirm it lands on the right season string near season boundaries
(e.g. very early October, or deep into a playoff-adjacent June) before
trusting it unattended.

FIXED 8/21/26: the per-team try/except never called conn.rollback() on
failure -- same bug class already found and fixed in
load_daily_game_logs.py on 8/10/26. Without it, one failed team insert
poisons the whole transaction (Postgres refuses further commands until
rolled back), so every team AFTER the failed one would also fail, even
though their data was fine. Added the same rollback the sibling file
already has.

DATE RANGE SUPPORT (added 8/21/26, same reasoning as
load_daily_game_logs.py): a single day was the only unit this could
process -- no way to catch up several missing days at once. run_for_date()
now holds the per-day work, called once per day in the requested range.

FIXED 8/21/26: added a delay between per-team API calls -- this file
never had one, in either its original version or the first edit
tonight. 30 requests to stats.nba.com back-to-back with zero pause
(worse right after load_daily_game_logs.py just hit the same host
repeatedly) is a very plausible cause of the read timeouts seen in
testing. Matches the existing project convention (0.6s in
load_game_logs.py, 0.3s in backfill_sleeper_league.py) rather than
inventing a new number.

Run:
    python scripts/load_daily_team_schedule.py                        (yesterday only, unchanged default)
    python scripts/load_daily_team_schedule.py YYYY-MM-DD              (single day, unchanged)
    python scripts/load_daily_team_schedule.py YYYY-MM-DD YYYY-MM-DD   (inclusive range, new)
"""

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nba_api.stats.static import teams as nba_teams
from get_team_schedule import get_team_schedule
from data_cleaning_team_schedule import clean_team_schedule
from load_team_schedule import load_team_schedule, TEAM_SCHEDULE_COLUMNS, build_team_lookup
from db_connection import get_connection

SLEEP_SECONDS_BETWEEN_CALLS = 0.6  # matches load_game_logs.py's convention


def season_for_date(d: date) -> str:
    """'2026-02-14' -> '2025-26'. Oct-cutoff heuristic -- see file docstring."""
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def daterange(start_date: date, end_date: date):
    """Inclusive date range, start_date..end_date -- yields one date() per day."""
    for n in range((end_date - start_date).days + 1):
        yield start_date + timedelta(days=n)


def run_for_date(target_date: date) -> int:
    """All the per-day work: pull each team's schedule row for this exact
    date. Returns total rows inserted for this date."""
    date_str = target_date.strftime("%m/%d/%Y")  # nba_api's expected format
    season = season_for_date(target_date)

    print(f"Loading team_schedule for {target_date.isoformat()} (season {season})...")

    team_lookup = build_team_lookup()
    all_teams = nba_teams.get_teams()

    conn = get_connection()
    total_inserted = 0
    failures = []

    for i, t in enumerate(all_teams, start=1):
        try:
            raw = get_team_schedule(t["id"], season, date_from=date_str, date_to=date_str)
            if raw.empty:
                print(f"[{i}/{len(all_teams)}] {t['full_name']}: no game this date")
                continue

            cleaned = clean_team_schedule(raw, team_lookup)
            cleaned = cleaned.rename(columns={
                "SEASON_ID": "season_id", "TEAM_ID": "team_id", "GAME_ID": "game_id",
                "GAME_DATE": "game_date", "WL": "wl", "PTS": "pts", "PLUS_MINUS": "plus_minus",
            })[TEAM_SCHEDULE_COLUMNS]

            n = load_team_schedule(cleaned, conn)
            total_inserted += n
            print(f"[{i}/{len(all_teams)}] {t['full_name']}: {n} rows")
        except Exception as e:
            print(f"[{i}/{len(all_teams)}] {t['full_name']}: FAILED — {e}")
            failures.append((t["full_name"], str(e)))
            # Same fix as load_daily_game_logs.py (8/10/26): without this,
            # one failed insert poisons the WHOLE transaction -- every
            # subsequent team in this run fails too, even though their
            # data was fine.
            conn.rollback()

        time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

    conn.close()

    print(f"Inserted {total_inserted} row(s) for {target_date.isoformat()}.")
    if failures:
        print(f"{len(failures)} team(s) failed:")
        for name, err in failures:
            print(f"  - {name}: {err}")

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


if __name__ == "__main__":
    main()
