"""
scripts/ingestion/backfill_manual_player_scores.py

Loads the manually-verified "player_scores" sheet from the xlsx files
into the real player_scores table (schema/tables/player_scores.sql) --
the missing ingestion step v3.1 never built. This is the player-level
counterpart to backfill_manual_team_points.py.

Plain upsert (ON CONFLICT DO UPDATE), NOT backfill_manual_team_points.py's
append-only change-detection pattern: that pattern exists specifically
to preserve history against Sleeper's own unreliable live sync
overwriting team_scores values over time. player_scores has no live-sync
counterpart at all -- it's a one-time manual backfill from an
already-validated xlsx -- so there's no competing live value to detect
drift against, and a plain upsert is the correct, simpler choice here.

Sentinel handling matches verify_player_scores_against_xlsx.py exactly
(same file, same sheet, just writing instead of only checking): 'BYE'
and NULL sleeper_player_id values are bracket-eliminated-roster
placeholders, skipped entirely, not inserted as a placeholder row --
same precedent as backfill_manual_team_points.py skipping 'BYE' rows
(sleeper_matchups.matchup_id IS NULL already represents "no opponent"
structurally). A recorded points=0 IS a real, legitimate DNP-after-lock
outcome and IS stored.

Does NOT re-verify data quality here (real score mismatches,
unresolved crosswalk gaps) -- that's verify_player_scores_against_xlsx.py's
job, already run and confirmed 0 real mismatches during v3.1. This
script trusts the xlsx as already-validated ground truth. Rerun the
verify script first if the xlsx has changed since that validation.

Requires: pandas + openpyxl (pip install pandas openpyxl --break-system-packages)

Run:
    python scripts/ingestion/backfill_manual_player_scores.py path/to/player_scores.xlsx
"""

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

import pandas as pd

from db_connection import get_connection

SENTINEL_VALUES = {"NULL", "BYE"}

# Season -> league_id, same confirmed dynasty chain used in
# backfill_manual_team_points.py.
SEASON_TO_LEAGUE_ID = {
    2024: "1113487058661744640",
    2025: "1214984705477185536",
}


def load_player_scores(xlsx_path: str) -> pd.DataFrame:
    df = pd.read_excel(xlsx_path, sheet_name="player_scores")
    # Same trailing-empty-rows trim as backfill_manual_team_points.py.
    df = df.dropna(subset=["season"])
    return df


def upsert_player_scores(conn, df: pd.DataFrame) -> dict:
    cur = conn.cursor()
    stats = {"upserted": 0, "skipped_sentinel": 0, "skipped_bad_points": 0, "skipped_unknown_season": 0}

    for row in df.itertuples(index=False):
        season = int(row.season)
        week = int(row.week_number)
        roster_id = int(row.roster_id)
        sleeper_player_id = row.sleeper_player_id
        points = row.points

        league_id = SEASON_TO_LEAGUE_ID.get(season)
        if league_id is None:
            print(f"WARNING: unknown season {season} (week={week}, roster_id={roster_id}) -- skipping, "
                  f"add it to SEASON_TO_LEAGUE_ID if this is a real season.")
            stats["skipped_unknown_season"] += 1
            continue

        # Same sentinel handling as verify_player_scores_against_xlsx.py --
        # BYE/NULL sleeper_player_id values are bracket-eliminated
        # placeholders, skipped entirely, never stored.
        if pd.isna(sleeper_player_id):
            sid_str = None
        else:
            sid_str = str(sleeper_player_id).strip()

        if sid_str is None or sid_str.upper() in SENTINEL_VALUES:
            stats["skipped_sentinel"] += 1
            continue

        sleeper_player_id = str(int(float(sid_str))) if sid_str.replace(".", "", 1).isdigit() else sid_str

        if pd.isna(points):
            stats["skipped_bad_points"] += 1
            continue

        cur.execute("""
            INSERT INTO player_scores (league_id, week, roster_id, sleeper_player_id, points)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (league_id, week, roster_id, sleeper_player_id)
            DO UPDATE SET points = EXCLUDED.points, synced_at = now();
        """, (league_id, week, roster_id, sleeper_player_id, float(points)))
        stats["upserted"] += 1

    conn.commit()
    cur.close()
    return stats


def main():
    parser = argparse.ArgumentParser(description="Upsert verified manual player scores into player_scores.")
    parser.add_argument("xlsx_path", help="Path to the player_scores .xlsx file")
    args = parser.parse_args()

    df = load_player_scores(args.xlsx_path)
    print(f"Loaded {len(df)} rows from {args.xlsx_path}.")

    conn = get_connection()
    stats = upsert_player_scores(conn, df)
    conn.close()

    print(f"\nUpserted {stats['upserted']} row(s).")
    print(f"Skipped {stats['skipped_sentinel']} sentinel row(s) (BYE / NULL placeholders).")
    print(f"Skipped {stats['skipped_bad_points']} row(s) with a missing points value.")
    if stats["skipped_unknown_season"]:
        print(f"WARNING: skipped {stats['skipped_unknown_season']} row(s) with an unrecognized season -- check SEASON_TO_LEAGUE_ID.")


if __name__ == "__main__":
    main()
