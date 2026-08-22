"""
scripts/sleeper/test_crosswalk_name_collisions.py

Tests docs/architecture_risks.md #7 (crosswalk is name-only, no collision
handling): checks whether any two DIFFERENT nba_player_ids already share
the same normalized name -- BEFORE the 2026-27 season's crosswalk work.
This is the exact silent-failure scenario the risk describes:
build_sleeper_player_crosswalk.py's matching only catches a collision as
"ambiguous" when BOTH candidates are present in `players` at match time.
If only one of two same-named players ever gets backfilled for the
seasons this project covers, the match looks clean (one candidate, no
ambiguity flagged) but could silently be attached to the wrong real
player.

Scoped to players with game_logs data BEFORE season 2026-27
(season_id < '22026') -- this checks the existing, already-live pool the
crosswalk draws from for prior seasons. This season's new rookies get
their own natural ambiguous-match check the first time they're actually
crosswalked, so they're intentionally excluded here.

Reuses normalize_name() directly from build_sleeper_player_crosswalk.py
rather than reimplementing it -- this test needs to use the EXACT same
normalization logic production uses, or it isn't really testing what it
claims to.

Run from the project root:
    python scripts/sleeper/test_crosswalk_name_collisions.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

sys.path.append(str(Path(__file__).resolve().parent))
from build_sleeper_player_crosswalk import normalize_name


def get_pre_2026_players(cur):
    """Every player_id/full_name with at least one game_logs row in a
    season before 2026-27 -- the pool this project's crosswalk actually
    draws from for prior seasons."""
    cur.execute("""
        SELECT DISTINCT p.player_id, p.full_name
        FROM players p
        JOIN game_logs gl ON gl.player_id = p.player_id
        WHERE gl.season_id < '22026'
    """)
    return cur.fetchall()


def find_collisions(players):
    """Groups players by BOTH normalized forms the real crosswalk tries
    (suffix-intact first, suffix-stripped fallback) -- a collision on
    EITHER form is a real risk, since build_sleeper_player_crosswalk.py
    falls back to the stripped form whenever the intact form finds
    nothing."""
    exact_groups = {}
    stripped_groups = {}

    for player_id, full_name in players:
        exact_key = normalize_name(full_name, strip_suffix=False)
        stripped_key = normalize_name(full_name, strip_suffix=True)
        exact_groups.setdefault(exact_key, []).append((player_id, full_name))
        stripped_groups.setdefault(stripped_key, []).append((player_id, full_name))

    exact_collisions = {k: v for k, v in exact_groups.items() if len(v) > 1}
    stripped_collisions = {k: v for k, v in stripped_groups.items() if len(v) > 1}
    return exact_collisions, stripped_collisions


def main():
    conn = get_connection()
    cur = conn.cursor()
    players = get_pre_2026_players(cur)
    cur.close()
    conn.close()

    print(f"{len(players)} distinct players with game_logs data before season 2026-27.\n")

    exact_collisions, stripped_collisions = find_collisions(players)

    print(f"=== Exact-normalized-name collisions: {len(exact_collisions)} ===")
    for key, group in exact_collisions.items():
        print(f"  '{key}':")
        for player_id, full_name in group:
            print(f"    player_id={player_id}  full_name={full_name!r}")

    print(f"\n=== Suffix-stripped-name collisions: {len(stripped_collisions)} ===")
    for key, group in stripped_collisions.items():
        if key in exact_collisions:
            continue  # already reported above, don't duplicate
        print(f"  '{key}':")
        for player_id, full_name in group:
            print(f"    player_id={player_id}  full_name={full_name!r}")

    if not exact_collisions and not stripped_collisions:
        print("\nPASS: no name collisions found in the existing player pool. "
              "The crosswalk's ambiguous-match check remains untested against "
              "a real case, but there's currently no live risk from this "
              "specific scenario.")
    else:
        total = len(exact_collisions) + len(stripped_collisions)
        print(f"\nFAIL: {total} real collision(s) found. If any Sleeper player "
              "ever matches one of these normalized names, verify manually "
              "which real player it actually is -- do not trust an "
              "unambiguous-looking match.")


if __name__ == "__main__":
    main()
