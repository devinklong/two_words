"""
Backfills one player's game log for one season -- for when the full roster
loop hits an isolated failure and rerunning all 500+ players isn't worth it.
Run: python scripts/backfill_single_player.py "PLAYER FULL NAME" SEASON
Example: python scripts/backfill_single_player.py "Alex Caruso" 2022-23
"""

import sys

from nba_api.stats.static import players as nba_players

from load_game_logs import build_team_lookup, fetch_and_clean_one_player, load_game_logs
from db_connection import get_connection


def main():
    if len(sys.argv) < 3:
        print('Usage: python scripts/backfill_single_player.py "PLAYER FULL NAME" SEASON')
        sys.exit(1)

    full_name = sys.argv[1]
    season = sys.argv[2]

    matches = nba_players.find_players_by_full_name(full_name)
    if not matches:
        print(f"No player found matching '{full_name}'")
        sys.exit(1)
    if len(matches) > 1:
        print(f"Multiple matches for '{full_name}':")
        for m in matches:
            print(f"  {m['full_name']} (id {m['id']})")
        sys.exit(1)

    player_id = matches[0]["id"]
    print(f"Found {matches[0]['full_name']} (player_id {player_id}). Fetching {season}...")

    team_lookup = build_team_lookup()
    conn = get_connection()

    try:
        cleaned = fetch_and_clean_one_player(player_id, season, team_lookup)
        n = load_game_logs(cleaned, conn)
        print(f"Inserted {n} rows for {matches[0]['full_name']}, {season}.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
