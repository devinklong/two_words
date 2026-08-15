"""
scripts/backfill_sleeper_points_snapshots.py

One-time backfill of sleeper_matchup_points_snapshots for both completed
seasons (2024, 2025), all 24 weeks each. Reuses
sync_matchup_points_snapshot() from sleeper_daily_sync.py unchanged --
since the table starts empty, every roster/week's points value is "new"
on this first run, so the same change-detecting function that will later
run daily during a live season does the full backfill here too, no
separate insert logic needed.

Does NOT touch the 2026-27 league -- no weeks have been played yet, so
there's nothing to backfill there (see sleeper_daily_sync.py for the
going-forward path once that season starts).

Usage: python scripts/backfill_sleeper_points_snapshots.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection
from sleeper_daily_sync import sync_matchup_points_snapshot

COMPLETED_LEAGUE_IDS = [
    ("1113487058661744640", "2024"),
    ("1214984705477185536", "2025"),
]
MAX_WEEK = 24  # same as backfill_sleeper_league.py -- 21 regular + 3 playoff


def run():
    conn = get_connection()
    cur = conn.cursor()

    for league_id, season in COMPLETED_LEAGUE_IDS:
        print(f"\n--- Season {season} (league_id={league_id}) ---")
        n_checked, n_inserted = sync_matchup_points_snapshot(cur, league_id, range(1, MAX_WEEK + 1))
        conn.commit()
        print(f"  {n_checked} roster/week points checked, {n_inserted} snapshot(s) recorded")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    run()
