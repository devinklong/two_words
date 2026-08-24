"""
scripts/inspect_all_fantasy_position_values.py

ONE-OFF DIAGNOSTIC. Answers a narrow question: does any player's
fantasy_positions array ever contain a generic/group label (F, G, UTIL)
in addition to the 5 specific positions (PG, SG, SF, PF, C)? Those
generic labels definitely exist as ROSTER SLOT types (roster_positions
has G/F/UTIL flex slots) -- this checks whether they also ever show up
on the PLAYER side, which would be a real wrinkle for the planned
sleeper_player_fantasy_positions child table design.

Run:
    python scripts/inspect_all_fantasy_position_values.py
"""

import requests

BASE_URL = "https://api.sleeper.app/v1"


def run():
    print("Fetching /players/nba (full directory, one call)...")
    resp = requests.get(f"{BASE_URL}/players/nba")
    resp.raise_for_status()
    all_players = resp.json()

    all_position_values = set()
    example_for_value = {}

    for sleeper_id, p in all_players.items():
        fantasy_positions = p.get("fantasy_positions") or []
        for pos in fantasy_positions:
            all_position_values.add(pos)
            if pos not in example_for_value:
                example_for_value[pos] = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}"

    print(f"\n{len(all_position_values)} distinct value(s) ever seen in any player's fantasy_positions:")
    for pos in sorted(all_position_values):
        print(f"  {pos!r} -- e.g. {example_for_value[pos]}")


if __name__ == "__main__":
    run()
