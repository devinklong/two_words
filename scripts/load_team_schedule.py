"""
Populate team_schedule by pulling each team's regular season schedule via
get_team_schedule(), cleaning with clean_team_schedule(), and bulk-inserting
into Postgres.

Prereq: schema/team_schedule.sql has been run — its CREATE TABLE already
defines PK (game_id, team_id) directly. (Older copies of this table used a
solo game_id PK, fixed via fix_team_schedule_pk.sql — not needed for a
fresh setup, since the composite PK is now baked into team_schedule.sql
itself.) Without the composite PK, every team's second-side row for a game
gets silently dropped by ON CONFLICT DO NOTHING.

Run from the project root:
    python scripts/load_team_schedule.py [SEASON]

Example:
    python scripts/load_team_schedule.py 2025-26
"""

import sys
import time

import pandas as pd
from psycopg2.extras import execute_values

from nba_api.stats.static import teams as nba_teams

from get_team_schedule import get_team_schedule
from data_cleaning_team_schedule import clean_team_schedule
from db_connection import get_connection

# Columns must match team_schedule.sql exactly, in order, lowercase
TEAM_SCHEDULE_COLUMNS = [
    "game_id", "season_id", "team_id", "opponent_team_id",
    "game_date", "is_home", "wl", "pts", "plus_minus",
]

SLEEP_SECONDS_BETWEEN_CALLS = 0.6  # be polite to the unofficial nba_api endpoints


def build_team_lookup() -> dict:
    all_teams = nba_teams.get_teams()
    return {t["abbreviation"]: t["id"] for t in all_teams}


def fetch_and_clean_one_team(team_id: int, season: str, team_lookup: dict) -> pd.DataFrame:
    raw = get_team_schedule(team_id, season)
    if raw.empty:
        return raw

    cleaned = clean_team_schedule(raw, team_lookup)

    # Rename to match DDL exactly. TEAM_ID survives clean_team_schedule()
    # untouched (it's the raw leaguegamefinder column), same as
    # opponent_team_id/is_home which clean_team_schedule() already derives.
    cleaned = cleaned.rename(columns={
        "SEASON_ID": "season_id",
        "TEAM_ID": "team_id",
        "GAME_ID": "game_id",
        "GAME_DATE": "game_date",
        "WL": "wl",
        "PTS": "pts",
        "PLUS_MINUS": "plus_minus",
    })

    missing = [c for c in TEAM_SCHEDULE_COLUMNS if c not in cleaned.columns]
    if missing:
        raise ValueError(f"clean_team_schedule() output is missing expected columns: {missing}")

    # leaguegamefinder returns extra columns (TEAM_ABBREVIATION, TEAM_NAME,
    # MIN, FGM, FGA, etc.) not yet in team_schedule.sql — select only what
    # the table actually has.
    return cleaned[TEAM_SCHEDULE_COLUMNS]


def load_team_schedule(df: pd.DataFrame, conn) -> int:
    if df.empty:
        return 0

    cur = conn.cursor()
    rows = list(df.itertuples(index=False, name=None))

    execute_values(
        cur,
        f"""
        INSERT INTO team_schedule ({", ".join(TEAM_SCHEDULE_COLUMNS)})
        VALUES %s
        ON CONFLICT (game_id, team_id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    cur.close()
    return len(rows)


def main():
    season = sys.argv[1] if len(sys.argv) > 1 else "2025-26"

    team_lookup = build_team_lookup()
    all_teams = nba_teams.get_teams()
    print(f"Loading team schedules for {len(all_teams)} teams, season {season}...")

    conn = get_connection()
    total_inserted = 0
    failures = []

    for i, t in enumerate(all_teams, start=1):
        team_id = t["id"]
        try:
            cleaned = fetch_and_clean_one_team(team_id, season, team_lookup)
            n = load_team_schedule(cleaned, conn)
            total_inserted += n
            print(f"[{i}/{len(all_teams)}] {t['full_name']}: {n} rows")
        except Exception as e:
            print(f"[{i}/{len(all_teams)}] {t['full_name']}: FAILED — {e}")
            failures.append((t["full_name"], str(e)))

        time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

    conn.close()

    print(f"\nDone. Inserted {total_inserted} total rows.")
    if failures:
        print(f"\n{len(failures)} team(s) failed:")
        for name, err in failures:
            print(f"  - {name}: {err}")


if __name__ == "__main__":
    main()
