"""
scripts/ingestion/add_player_by_id.py

Adds one player to nba_players by NBA player id, using the live
commonplayerinfo endpoint. For players who debuted after nba_api's bundled
static list (load_players.py) was built -- typically 2026 rookies whose first
game_logs insert fails the game_logs_player_id_fkey foreign key.
Added 10/21/26. Idempotent (ON CONFLICT DO NOTHING).

Run (from project root):
    PYTHONPATH=scripts python scripts/ingestion/add_player_by_id.py 1643551 [more ids...]
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from nba_api.stats.endpoints import commonplayerinfo
from db_connection import get_connection


def add_player(player_id, cur):
    d = commonplayerinfo.CommonPlayerInfo(player_id=player_id, timeout=60).get_data_frames()[0]
    if d.empty:
        raise ValueError(f"commonplayerinfo returned nothing for {player_id}")
    r = d.iloc[0]
    cur.execute(
        """
        INSERT INTO nba_players (player_id, full_name, first_name, last_name, is_active)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (player_id) DO NOTHING
        """,
        (int(player_id), r["DISPLAY_FIRST_LAST"], r["FIRST_NAME"], r["LAST_NAME"], str(r["ROSTERSTATUS"]) in ("Active", "1")),
    )
    return r["DISPLAY_FIRST_LAST"]


def main():
    ids = [int(a) for a in sys.argv[1:]]
    if not ids:
        sys.exit("usage: add_player_by_id.py PLAYER_ID [PLAYER_ID ...]")
    conn = get_connection()
    cur = conn.cursor()
    for pid in ids:
        print(f"Added {pid}: {add_player(pid, cur)}")
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
