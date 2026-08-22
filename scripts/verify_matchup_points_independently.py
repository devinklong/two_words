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

A mismatch here reflects the documented Sleeper matchup-points API
instability (Step 6), not necessarily a stored-data error -- stored
data (sleeper_matchup_points_snapshots) has already been hand-verified
against the app's real Schedule screens. Treat this script's output as
informational, not as evidence something needs fixing in the DB.

CENTRALIZED 8/22/26 (docs/architecture_risks.md #8): MAX_WEEK now
imported from scripts/constants.py instead of redefined here -- no
behavior change, same literal value as before.

FIXED 8/22/26: the comparison used to check `fresh_points ==
stored_points` directly -- but stored_points comes back from Postgres
as a Decimal and fresh_points is a JSON-sourced Python float, and
those don't compare equal even for identical values
(Decimal('393.3') == 393.3 is False due to float binary imprecision).
This was producing large numbers of false-positive mismatches. Now
both sides are rounded to 2 decimals as float before comparing.

Usage: python scripts/verify_matchup_points_independently.py <league_id>
"""

import sys
import time
import requests
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection
from constants import MAX_WEEK

BASE_URL = "https://api.sleeper.app/v1"
REQUEST_DELAY = 0.3


def close_enough(a, b, tol=0.01):
    """Round-then-compare, avoiding the Decimal-vs-float false-mismatch
    bug the old version of this script had (Decimal('393.3') == 393.3
    is False due to float binary imprecision)."""
    if a is None or b is None:
        return a == b
    return abs(round(float(a), 2) - round(float(b), 2)) < tol


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

            if close_enough(fresh_points, stored_points):
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
