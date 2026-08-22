"""
Backfills players present in game_logs but missing from players -- happens
when nba_api's bundled static player list (load_players.py) hasn't caught
up to very recent additions that the live endpoints already know about.

CHAINED (8/10/26): after inserting missing players, also runs
sync_game_fantasy_scores_weekly_effective() -- if any of those players'
game_logs rows existed but never got a corresponding effective-table row
(e.g. skipped because a join depending on the player_tiers/pool chain
failed while the player was still missing from players), this catches
them up. A no-op if there's nothing to catch up on.

ADDED 8/21/26: an optional player_id argument, for a real gap this
script's normal detection can't cover. find_missing_player_ids() only
finds players already present in game_logs -- but a brand-new player's
FIRST game_logs insert fails outright with a foreign-key violation
(since they're not in players yet), so Postgres rolls the whole insert
back and they never make it into game_logs at all. Auto-detection can't
find someone who was never actually written anywhere. Confirmed live
8/21/26 with a rookie (Toby Okani) active in real April 2026 games, not
yet in either players or game_logs. Passing a specific player_id
bypasses detection and inserts that one player directly, using the same
fetch/insert logic as the normal run.

ALSO FIXED 8/21/26: sys.path.append() was placed AFTER local imports
that depend on it (db_connection, sync_game_fantasy_scores_weekly_
effective) -- same ordering bug already found and fixed in three other
files the same night, missed here too on the first pass despite editing
this exact file for the player_id argument above. Moved to the top,
before any local import.

Run:
    python scripts/backfill_missing_players.py                (normal: auto-detect from game_logs)
    python scripts/backfill_missing_players.py PLAYER_ID        (new: backfill one specific player_id directly)
"""

import time
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nba_api.stats.endpoints import commonplayerinfo
from psycopg2.extras import execute_values

from db_connection import get_connection
from sync_game_fantasy_scores_weekly_effective import sync_game_fantasy_scores_weekly_effective

SLEEP_SECONDS_BETWEEN_CALLS = 0.6


def find_missing_player_ids(conn) -> list[int]:
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT gl.player_id
        FROM game_logs gl
        LEFT JOIN players p ON p.player_id = gl.player_id
        WHERE p.player_id IS NULL
    """)
    ids = [row[0] for row in cur.fetchall()]
    cur.close()
    return ids


def fetch_player_info(player_id: int) -> dict:
    info = commonplayerinfo.CommonPlayerInfo(player_id=player_id).get_data_frames()[0]
    row = info.iloc[0]
    return {
        "player_id": player_id,
        "full_name": row["DISPLAY_FIRST_LAST"],
        "first_name": row["FIRST_NAME"],
        "last_name": row["LAST_NAME"],
        "is_active": bool(row["ROSTERSTATUS"] == 1) if "ROSTERSTATUS" in row else True,
    }


def insert_players(conn, results: list[dict]):
    if not results:
        return
    cur = conn.cursor()
    rows = [(r["player_id"], r["full_name"], r["first_name"], r["last_name"], r["is_active"]) for r in results]
    execute_values(
        cur,
        """
        INSERT INTO players (player_id, full_name, first_name, last_name, is_active)
        VALUES %s
        ON CONFLICT (player_id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    cur.close()
    print(f"\nInserted {len(rows)} player(s).")


def main():
    conn = get_connection()

    if len(sys.argv) > 1:
        # Direct mode: backfill one specific player_id, bypassing
        # detection -- see module docstring for why detection alone
        # can't find someone whose game_logs insert never succeeded.
        player_id = int(sys.argv[1])
        print(f"Fetching player_id {player_id} directly (bypassing game_logs detection)...")
        try:
            info = fetch_player_info(player_id)
            print(f"Found {info['full_name']} (id {player_id})")
            insert_players(conn, [info])
        except Exception as e:
            print(f"player_id {player_id}: FAILED — {e}")
            conn.close()
            sys.exit(1)
    else:
        missing_ids = find_missing_player_ids(conn)
        print(f"Found {len(missing_ids)} player_id(s) in game_logs missing from players.")

        if not missing_ids:
            print("Nothing to backfill.")
            conn.close()
            return

        results = []
        for i, pid in enumerate(missing_ids, start=1):
            try:
                info = fetch_player_info(pid)
                results.append(info)
                print(f"[{i}/{len(missing_ids)}] {info['full_name']} (id {pid})")
            except Exception as e:
                print(f"[{i}/{len(missing_ids)}] player_id {pid}: FAILED — {e}")

            time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

        insert_players(conn, results)

    print("\nSyncing game_fantasy_scores_weekly_effective (catches up any of these")
    print("players' games that were skipped while they were missing)...")
    n_synced = sync_game_fantasy_scores_weekly_effective(conn)
    print(f"Synced {n_synced} new row(s).")

    conn.close()


if __name__ == "__main__":
    main()
