"""
scripts/ingestion/load_current_teams.py

Refreshes nba_player_current_team from nba_api CommonAllPlayers (one call).
Added 10/21/26. The table is a snapshot: the whole table is replaced in one
transaction, so a player who left the list does not keep a stale team.

Sanity gate before any write: at least 400 players with a team and all 30 teams
present; otherwise nothing is written (override only with --force).

Run (from project root):
    python scripts/ingestion/load_current_teams.py            (dry run: prints counts)
    python scripts/ingestion/load_current_teams.py --write
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

REAL_TEAM_IDS = set(range(1610612737, 1610612767))


def season_string(today):
    """NBA season containing `today`: Oct-Dec belongs to the season starting that year."""
    start = today.year if today.month >= 10 else today.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def rows_from_frame(df):
    """[(player_id, team_id, roster_status)] from a CommonAllPlayers frame."""
    return [(int(r.PERSON_ID), int(r.TEAM_ID or 0), int(r.ROSTERSTATUS or 0)) for r in df.itertuples()]


def sanity(rows):
    problems = []
    with_team = [r for r in rows if r[1] in REAL_TEAM_IDS]
    teams = {r[1] for r in with_team}
    if len(with_team) < 400:
        problems.append(f"only {len(with_team)} players with an NBA team (expected 400 or more)")
    if len(teams) != 30:
        problems.append(f"{len(teams)} distinct teams (expected 30)")
    return problems


def main():
    ap = argparse.ArgumentParser(description="Refresh nba_player_current_team.")
    ap.add_argument("--season", default=season_string(date.today()), help="e.g. 2026-27")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    from nba_api.stats.endpoints import commonallplayers
    df = commonallplayers.CommonAllPlayers(is_only_current_season=1, league_id="00", season=args.season, timeout=60).get_data_frames()[0]
    rows = rows_from_frame(df)
    with_team = sum(1 for r in rows if r[1] in REAL_TEAM_IDS)
    print(f"{len(rows)} players returned for {args.season}; {with_team} on an NBA team; {len(rows) - with_team} without one.")

    problems = sanity(rows)
    if problems:
        print("SANITY GATE FAILED:")
        for p in problems:
            print("  -", p)
        if not args.force:
            print("Nothing written.")
            sys.exit(1)
        print("--force given: continuing anyway.")

    if not args.write:
        print("Dry run: nothing written. Re-run with --write.")
        return

    from psycopg2.extras import execute_values
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM nba_player_current_team")
    execute_values(cur, "INSERT INTO nba_player_current_team (player_id, team_id, roster_status) VALUES %s", rows)
    conn.commit()
    cur.execute("SELECT COUNT(*) FROM nba_player_current_team")
    print(f"nba_player_current_team now has {cur.fetchone()[0]} rows.")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
