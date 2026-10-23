"""
scripts/inspect_position_vs_fantasy_positions.py

ONE-OFF DIAGNOSTIC, not part of the pipeline. build_sleeper_player_crosswalk.py
stores Sleeper's SINGULAR `position` field into sleeper_position -- but
Sleeper's real player data also carries a separate `fantasy_positions`
array, which can hold MORE than one value for a multi-position-eligible
player (e.g. someone eligible at both PG and SG). The crosswalk currently
throws that multi-eligibility information away.

This checks how often that actually matters in practice: for every real
player already in sleeper_player_crosswalk (i.e. someone who has actually
been rostered/matched-up/transacted in this league -- not Sleeper's full
~500-player NBA universe), compares `position` against `fantasy_positions`
and reports how many diverge, and how.

Run:
    python scripts/inspect_position_vs_fantasy_positions.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import requests

BASE_URL = "https://api.sleeper.app/v1"


def fetch_all_nba_players():
    print("Fetching /players/nba (full directory, one call)...")
    resp = requests.get(f"{BASE_URL}/players/nba")
    resp.raise_for_status()
    return resp.json()


def get_crosswalked_sleeper_ids(cur):
    cur.execute("SELECT sleeper_player_id, sleeper_position, sleeper_full_name FROM sleeper_player_crosswalk;")
    return cur.fetchall()


def run():
    conn = get_connection()
    cur = conn.cursor()

    crosswalked = get_crosswalked_sleeper_ids(cur)
    print(f"{len(crosswalked)} players in sleeper_player_crosswalk to check.")

    all_players = fetch_all_nba_players()

    single_position_matches_fp = 0    # position == the only entry in fantasy_positions
    multi_eligible_collapsed = 0      # fantasy_positions has >1 real position, position picked just one
    position_missing_from_fp = 0      # position isn't even IN fantasy_positions (worth a look)
    no_fantasy_positions_data = 0     # fantasy_positions missing/empty entirely
    not_found_in_directory = 0

    multi_eligible_examples = []
    mismatch_examples = []

    for sleeper_id, stored_position, full_name in crosswalked:
        p = all_players.get(sleeper_id)
        if p is None:
            not_found_in_directory += 1
            continue

        fantasy_positions = p.get("fantasy_positions") or []
        real_position = p.get("position")

        if not fantasy_positions:
            no_fantasy_positions_data += 1
            continue

        if real_position not in fantasy_positions:
            position_missing_from_fp += 1
            mismatch_examples.append((sleeper_id, full_name, real_position, fantasy_positions, stored_position))
            continue

        if len(fantasy_positions) > 1:
            multi_eligible_collapsed += 1
            if len(multi_eligible_examples) < 15:
                multi_eligible_examples.append((sleeper_id, full_name, real_position, fantasy_positions, stored_position))
        else:
            single_position_matches_fp += 1

    cur.close()
    conn.close()

    total_checked = len(crosswalked) - not_found_in_directory - no_fantasy_positions_data
    print(f"\n{total_checked} players had usable fantasy_positions data to compare.")
    print(f"{not_found_in_directory} not found in current /players/nba directory (retired/inactive since matching).")
    print(f"{no_fantasy_positions_data} had no fantasy_positions data at all.")
    print(f"\n{single_position_matches_fp} genuinely single-position (fantasy_positions has exactly 1 entry, matches stored position).")
    print(f"{multi_eligible_collapsed} are MULTI-POSITION ELIGIBLE -- stored sleeper_position silently picked just one "
          f"of {'>1'} real eligible positions.")
    print(f"{position_missing_from_fp} have a stored `position` that isn't even IN their own fantasy_positions list "
          f"-- worth a closer look, these are genuinely odd.")

    if multi_eligible_collapsed:
        pct = 100 * multi_eligible_collapsed / total_checked if total_checked else 0
        print(f"\nMulti-eligibility affects {pct:.1f}% of this league's real rostered player pool.")

    if multi_eligible_examples:
        print("\n=== Sample of multi-position-eligible players (sleeper_id, name, stored position, real fantasy_positions) ===")
        for sid, name, real_pos, fp, stored in multi_eligible_examples:
            print(f"  {sid}: {name} -- stored={stored!r}  real_fantasy_positions={fp}")

    if mismatch_examples:
        print("\n=== Players where stored position isn't even in their own fantasy_positions (worth investigating) ===")
        for sid, name, real_pos, fp, stored in mismatch_examples[:15]:
            print(f"  {sid}: {name} -- position={real_pos!r}  fantasy_positions={fp}  stored={stored!r}")


if __name__ == "__main__":
    run()
