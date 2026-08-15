"""
Daily version of load_team_schedule.py -- pulls only YESTERDAY's games
(default) for all 30 teams instead of a full season, using
get_team_schedule()'s new date_from/date_to params. Reuses
clean_team_schedule() and load_team_schedule() unchanged, so the insert
logic (ON CONFLICT DO NOTHING, composite PK) stays a single source of
truth with the full-backfill script.

FIRST-PASS/UNTESTED -- season is still a required nba_api param even when
date-filtering; SEASON_FOR_DATE() below is a simple Oct-cutoff heuristic
(NBA season "N-N+1" runs Oct through ~June) -- fine for in-season daily
runs, but confirm it lands on the right season string near season
boundaries (e.g. very early October, or deep into a playoff-adjacent June)
before trusting it unattended.

Run: python scripts/load_daily_team_schedule.py [YYYY-MM-DD]
     (defaults to yesterday if no date given)
"""

import sys
from datetime import date, timedelta

from nba_api.stats.static import teams as nba_teams
from pathlib import Path
from get_team_schedule import get_team_schedule
from data_cleaning_team_schedule import clean_team_schedule
from load_team_schedule import load_team_schedule, TEAM_SCHEDULE_COLUMNS, build_team_lookup
from db_connection import get_connection

sys.path.append(str(Path(__file__).resolve().parents[1]))

def season_for_date(d: date) -> str:
    """'2026-02-14' -> '2025-26'. Oct-cutoff heuristic -- see file docstring."""
    start_year = d.year if d.month >= 10 else d.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"


def main():
    target_date = (
        date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1
        else date.today() - timedelta(days=1)
    )
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

    conn.close()

    print(f"\nDone. Inserted {total_inserted} total rows for {target_date.isoformat()}.")
    if failures:
        print(f"\n{len(failures)} team(s) failed:")
        for name, err in failures:
            print(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
