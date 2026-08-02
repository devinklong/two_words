"""
Identifies which players in game_logs would NOT have been picked up by the
old get_active_players() approach — i.e. the players responsible for the
+47 row delta after switching to CommonTeamRoster-based season rosters.

Run from the project root:
    python scripts/check_roster_delta.py
"""

from nba_api.stats.static import players as nba_players
from db_connection import get_connection


def main():
    active_today_ids = {p["id"] for p in nba_players.get_active_players()}

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT gl.player_id, p.full_name, COUNT(*) AS rows_logged
        FROM game_logs gl
        JOIN players p ON p.player_id = gl.player_id
        GROUP BY gl.player_id, p.full_name
        ORDER BY gl.player_id
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    not_in_active = [(pid, name, n) for pid, name, n in rows if pid not in active_today_ids]

    print(f"{len(rows)} total distinct players in game_logs.")
    print(f"{len(not_in_active)} of them are NOT on today's active players list:\n")

    total_rows_from_these = 0
    for pid, name, n in not_in_active:
        print(f"  {name} (player_id {pid}): {n} rows")
        total_rows_from_these += n

    print(f"\nTotal rows from non-'active' players: {total_rows_from_these}")


if __name__ == "__main__":
    main()
