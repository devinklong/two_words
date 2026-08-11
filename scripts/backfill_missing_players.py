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

Run: python scripts/backfill_missing_players.py
"""

import time

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


def main():
    conn = get_connection()

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

    if results:
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
        print(f"\nInserted {len(rows)} missing players.")

    print("\nSyncing game_fantasy_scores_weekly_effective (catches up any of these")
    print("players' games that were skipped while they were missing)...")
    n_synced = sync_game_fantasy_scores_weekly_effective(conn)
    print(f"Synced {n_synced} new row(s).")

    conn.close()


if __name__ == "__main__":
    main()
