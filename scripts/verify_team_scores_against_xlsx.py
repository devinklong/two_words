"""
scripts/verify_team_scores_against_xlsx.py

Row-by-row comparison of the team_scores sheet in a manually-reconciled
xlsx against what's actually stored in sleeper_matchup_points_snapshots
right now. Unlike the Step 6 aggregate reconciliation (season totals:
wins/losses/ties/PF/PA), this checks every individual (season, week,
roster_id) row directly -- the aggregate match doesn't guarantee every
row underneath it is correct, only that errors (if any) canceled out
across the season.

Same dedup logic as sleeper_matchup_points_latest (most recent snapshot
per week/roster), reimplemented independently here to keep this script
fully separate from the rest of the pipeline, same convention as
verify_matchup_points_independently.py / verify_transactions_independently.py.

FIXED 8/23/26: the sheet uses 'BYE' as a literal points value for
bracket-eliminated weeks (same convention backfill_manual_team_points.py
already handles: skip BYE/NaN rows entirely, never insert or compare
them as a real score) -- the original version tried to float()-convert
every row unconditionally and crashed on the first BYE row it hit.

Usage: python scripts/verify_team_scores_against_xlsx.py <xlsx_path>
Example: python scripts/verify_team_scores_against_xlsx.py ~/Downloads/2024_2025_all_scores.xlsx
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

LEAGUE_ID_BY_SEASON = {
    2024: "1113487058661744640",
    2025: "1214984705477185536",
}


def close_enough(a, b, tol=0.01):
    """Round-then-compare, avoiding the Decimal-vs-float false-mismatch
    bug found earlier this session in verify_matchup_points_independently.py."""
    if a is None or b is None:
        return a == b
    return abs(round(float(a), 2) - round(float(b), 2)) < tol


def get_stored_team_scores(cur, league_id):
    cur.execute("""
        SELECT DISTINCT ON (week, roster_id) week, roster_id, points
        FROM sleeper_matchup_points_snapshots
        WHERE league_id = %s
        ORDER BY week, roster_id, synced_at DESC;
    """, (league_id,))
    return {(week, roster_id): points for week, roster_id, points in cur.fetchall()}


def run(xlsx_path):
    df = pd.read_excel(xlsx_path, sheet_name="team_scores")
    df = df.dropna(subset=["season"])

    conn = get_connection()
    cur = conn.cursor()

    all_mismatches = []
    all_missing_from_db = []
    all_xlsx_only_extra = []
    all_bye_rows = []

    for season, league_id in LEAGUE_ID_BY_SEASON.items():
        stored = get_stored_team_scores(cur, league_id)
        season_rows = df[df["season"] == season]

        xlsx_keys = set()
        for row in season_rows.itertuples():
            week = int(row.week_number)
            roster_id = int(row.roster_id)
            xlsx_points = row.points

            # BYE (bracket-eliminated week) is a real, expected sentinel
            # value here, not a data error -- skip entirely, same
            # convention backfill_manual_team_points.py already uses.
            if isinstance(xlsx_points, str) and xlsx_points.strip().upper() == "BYE":
                all_bye_rows.append((season, week, roster_id))
                continue
            if pd.isna(xlsx_points):
                all_bye_rows.append((season, week, roster_id))
                continue

            key = (week, roster_id)
            xlsx_keys.add(key)

            stored_points = stored.get(key)
            if stored_points is None:
                all_missing_from_db.append((season, week, roster_id, xlsx_points))
            elif not close_enough(stored_points, xlsx_points):
                all_mismatches.append((season, week, roster_id, stored_points, xlsx_points))

        db_only = set(stored.keys()) - xlsx_keys
        for week, roster_id in db_only:
            all_xlsx_only_extra.append((season, week, roster_id, stored[(week, roster_id)]))

        print(f"Season {season}: {len(season_rows)} xlsx rows, {len(stored)} stored rows checked.")

    cur.close()
    conn.close()

    print(f"\n{len(all_mismatches)} genuine value mismatches.")
    print(f"{len(all_missing_from_db)} xlsx rows with no matching DB row at all.")
    print(f"{len(all_xlsx_only_extra)} DB rows with no corresponding xlsx row (DB has extra data).")
    print(f"{len(all_bye_rows)} BYE rows (bracket-eliminated weeks, skipped, not checked).\n")

    if all_mismatches:
        print("season  week  roster_id  stored     xlsx")
        for season, week, roster_id, stored_val, xlsx_val in sorted(all_mismatches):
            print(f"{season:>6}  {week:>4}  {roster_id:>9}  {stored_val!s:>9}  {xlsx_val!s:>9}")
        print()

    if all_missing_from_db:
        print("xlsx rows missing from DB (season, week, roster_id, xlsx_points):")
        for row in sorted(all_missing_from_db):
            print(f"  {row}")
        print()

    if all_xlsx_only_extra:
        print("DB rows with no xlsx counterpart (season, week, roster_id, stored_points):")
        for row in sorted(all_xlsx_only_extra):
            print(f"  {row}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/verify_team_scores_against_xlsx.py <xlsx_path>")
        sys.exit(1)
    run(sys.argv[1])
