"""
scripts/verify_matchup_points_independently.py

Standalone re-verification, deliberately NOT importing anything from
backfill_sleeper_points_snapshots.py, sleeper_daily_sync.py, or
backfill_sleeper_league.py -- this exists to rule out a bug in THIS
project's own fetch/parsing logic, by pulling fresh data with completely
separate code and diffing it against what's already stored.

Does not explain WHY any mismatch exists (see project notes -- six
tested theories, all ruled out by evidence, as of 8/14/26). Only
produces a complete, systematic map of exactly which (week, roster_id)
pairs currently disagree between a fresh pull and stored data, across
every week of the season -- something only spot-checked so far.

Usage: python scripts/verify_matchup_points_independently.py <league_id>
"""

import sys
import time
import requests
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

BASE_URL = "https://api.sleeper.app/v1"
MAX_WEEK = 24
REQUEST_DELAY = 0.3


def fetch_week(league_id, week):
    resp = requests.get(f"{BASE_URL}/league/{league_id}/matchups/{week}")
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def get_stored_points(cur, league_id):
    """Most-recent stored snapshot per (week, roster_id) -- same dedup
    logic as sleeper_matchup_points_latest, reimplemented independently
    here rather than querying that view, to keep this script fully
    separate from anything else in the pipeline."""
    cur.execute("""
        SELECT DISTINCT ON (week, roster_id) week, roster_id, points
        FROM sleeper_matchup_points_snapshots
        WHERE league_id = %s
        ORDER BY week, roster_id, synced_at DESC;
    """, (league_id,))
    return {(week, roster_id): points for week, roster_id, points in cur.fetchall()}


def run(league_id):
    conn = get_connection()
    cur = conn.cursor()

    stored = get_stored_points(cur, league_id)
    cur.close()
    conn.close()

    print(f"{len(stored)} stored (week, roster_id) points loaded for league_id={league_id}\n")

    mismatches = []
    matches = 0

    for week in range(1, MAX_WEEK + 1):
        matchups = fetch_week(league_id, week)
        if not matchups:
            continue
        for m in matchups:
            roster_id = m["roster_id"]
            fresh_points = m.get("points")
            key = (week, roster_id)
            stored_points = stored.get(key)

            if stored_points is None:
                print(f"  [NO STORED VALUE] week={week} roster_id={roster_id} fresh={fresh_points}")
                continue

            if fresh_points == stored_points:
                matches += 1
            else:
                mismatches.append((week, roster_id, stored_points, fresh_points))

    print(f"\n{matches} matched, {len(mismatches)} mismatched.\n")
    if mismatches:
        print("week  roster_id  stored     fresh")
        for week, roster_id, stored_points, fresh_points in sorted(mismatches):
            print(f"{week:>4}  {roster_id:>9}  {stored_points!s:>9}  {fresh_points!s:>9}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/verify_matchup_points_independently.py <league_id>")
        sys.exit(1)
    run(sys.argv[1])
