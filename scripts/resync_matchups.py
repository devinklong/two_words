"""
scripts/resync_matchups.py

One-off resync for sleeper_matchups after its data got wiped by
re-running sleeper_matchups.sql's DROP TABLE. Re-pulls matchups for
both real league seasons via the existing backfill logic -- safe to
rerun anytime, upserts on conflict.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from backfill_sleeper_league import upsert_matchups
from db_connection import get_connection

LEAGUE_IDS = [
    "1113487058661744640",  # 2024
    "1214984705477185536",  # 2025
]


def run():
    conn = get_connection()
    cur = conn.cursor()

    for league_id in LEAGUE_IDS:
        n = upsert_matchups(cur, league_id)
        conn.commit()
        print(f"{league_id}: {n} matchup rows synced")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()
