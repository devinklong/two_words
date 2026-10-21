"""
scripts/sleeper/run_daily_sync.py

Command-line runner for sleeper_daily_sync.run(), which is importable logic
only (its own __main__ is a placeholder). Resolves the live league from
sleeper_current_league and the season from current_season_config, so
nothing is hardcoded and it self-updates each season.

Run (from project root):
    python scripts/sleeper/run_daily_sync.py
    python scripts/sleeper/run_daily_sync.py --as-of 2026-10-20
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection
from sleeper_daily_sync import run


def main():
    parser = argparse.ArgumentParser(description="Sync the live Sleeper league's current week.")
    parser.add_argument("--as-of", help="YYYY-MM-DD (default: today)")
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    row = cur.fetchone()
    if row is None:
        raise SystemExit("sleeper_current_league returned no rows -- check the previous_league_id chain.")
    league_id = row[0]
    cur.execute("SELECT season_id FROM current_season_config ORDER BY updated_at DESC LIMIT 1")
    row = cur.fetchone()
    if row is None:
        raise SystemExit("current_season_config is empty.")
    season_id = row[0]
    cur.close()
    conn.close()

    print(f"Syncing league_id={league_id}, season_id={season_id}, as_of={as_of or date.today()}")
    run(league_id, season_id, as_of)


if __name__ == "__main__":
    main()
