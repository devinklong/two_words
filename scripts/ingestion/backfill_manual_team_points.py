"""
scripts/ingestion/backfill_manual_team_points.py

Loads the manually-entered, hand-verified team_scores sheet into
sleeper_matchup_points_snapshots -- this is what actually replaces the
unreliable live-synced 2025-26 weeks 1-18 values (and confirms/reinforces
the already-correct 2024-25 values), per the Step 6 investigation.

Change-detection mirrors sleeper_daily_sync.py's sync_matchup_points_
snapshot(): only inserts a new snapshot row when the value actually
differs from the most recently recorded one for that (league_id, week,
roster_id) -- so re-running this script is a safe no-op, and a value
that's already correct doesn't generate a spurious extra row. Because
the table is an append-only change log (PK includes synced_at), a
genuinely different manual value naturally becomes the new "latest"
snapshot without deleting or modifying anything already there --
sleeper_matchup_points_latest / historical_matchup_results / playoff_
bracket_results all already pick the most-recent synced_at per row, so
they pick this up automatically once inserted.

'BYE' rows (14 total across both seasons -- see project notes) are
skipped entirely, not inserted as NULL or a placeholder -- a bye week
has no opponent, which sleeper_matchups.matchup_id IS NULL already
represents structurally.

Requires: pandas + openpyxl (pip install pandas openpyxl --break-system-packages)

Run:
    python scripts/sleeper/backfill_manual_team_points.py path/to/team_scores.xlsx
"""

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd

from db_connection import get_connection

# Season -> league_id, per the confirmed dynasty chain
SEASON_TO_LEAGUE_ID = {
    2024: "1113487058661744640",
    2025: "1214984705477185536",
}


def load_team_scores(xlsx_path: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path, sheet_name="team_scores")
    # The sheet's declared dimension extends well past the real data
    # (trailing empty rows) -- drop anything with no season value.
    df = df.dropna(subset=["season"])
    return df


def upsert_manual_points(conn, df: pd.DataFrame) -> dict:
    cur = conn.cursor()
    stats = {"inserted": 0, "skipped_bye": 0, "skipped_unchanged": 0, "skipped_unknown_season": 0}

    for row in df.itertuples(index=False):
        season = int(row.season)
        week = int(row.week_number)
        roster_id = int(row.roster_id)
        points = row.points

        if points == "BYE" or pd.isna(points):
            stats["skipped_bye"] += 1
            continue

        league_id = SEASON_TO_LEAGUE_ID.get(season)
        if league_id is None:
            print(f"WARNING: unknown season {season} (week={week}, roster_id={roster_id}) -- skipping, "
                  f"add it to SEASON_TO_LEAGUE_ID if this is a real season.")
            stats["skipped_unknown_season"] += 1
            continue

        points = float(points)

        # Only insert if this value actually differs from the most
        # recent snapshot already on record -- same pattern
        # sleeper_daily_sync.py's sync_matchup_points_snapshot() uses.
        cur.execute("""
            SELECT points FROM sleeper_matchup_points_snapshots
            WHERE league_id = %s AND week = %s AND roster_id = %s
            ORDER BY synced_at DESC LIMIT 1
        """, (league_id, week, roster_id))
        last = cur.fetchone()
        if last is not None and last[0] is not None and float(last[0]) == points:
            stats["skipped_unchanged"] += 1
            continue

        cur.execute("""
            INSERT INTO sleeper_matchup_points_snapshots
                (league_id, week, roster_id, points, starters_points, players_points)
            VALUES (%s, %s, %s, %s, NULL, NULL)
        """, (league_id, week, roster_id, points))
        stats["inserted"] += 1

    conn.commit()
    cur.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Upsert verified manual team scores into sleeper_matchup_points_snapshots.")
    parser.add_argument("xlsx_path", help="Path to the team_scores .xlsx file")
    args = parser.parse_args()

    df = load_team_scores(args.xlsx_path)
    print(f"Loaded {len(df)} rows from {args.xlsx_path}.")

    conn = get_connection()
    stats = upsert_manual_points(conn, df)
    conn.close()

    print(f"\nInserted {stats['inserted']} new snapshot(s) (real value changes).")
    print(f"Skipped {stats['skipped_bye']} BYE row(s) (no opponent that week).")
    print(f"Skipped {stats['skipped_unchanged']} row(s) already matching the latest recorded value.")
    if stats["skipped_unknown_season"]:
        print(f"WARNING: skipped {stats['skipped_unknown_season']} row(s) with an unrecognized season -- check SEASON_TO_LEAGUE_ID.")


if __name__ == "__main__":
    main()
