"""
scripts/batch_inspect_playbyplay_fouls.py

ONE-OFF DIAGNOSTIC, not part of the pipeline (same as
inspect_playbyplay_fouls.py, which this supersedes for the "find every
real subType" purpose -- that script stays useful for spot-checking one
specific game by hand, this one is for casting a wide net).

Technical fouls are rare enough that a handful of games won't surface
the full universe of labels NBA's API actually uses. Scans a real
sample of games and aggregates every distinct foul-related subType
seen, WITH the specific game_id/player detail for a few real examples
of each -- so an unconfirmed subType can be looked up directly and
spot-checked against a real Sleeper score, same discipline as the
confirmed cases so far: a subType containing the word "Technical" is
NOT enough on its own to assume it costs the real -2 (Defense 3 Second
proved that wrong -- team-attributed technicals appear not to carry
the penalty, only individual-attributed ones do, per findings below,
but that's still a hypothesis pending more confirmed cases).

UPDATED 8/23/26: now tracks game_id + game_date + personId/playerName
for up to 3 real example occurrences per subType (previously only kept
one description string with no way to look the game back up) --
needed so still-unconfirmed subTypes (Delay Technical, Too Many
Players Technical, Hanging Technical, as of this run) can actually be
checked against a real recorded Sleeper score, the same way Defense 3
Second (Jay Huff, game_id 0022400070) and the original 5 disciplinary
technicals were confirmed.

Flags each subType already confirmed one way or the other, so new,
still-unconfirmed labels are obvious in the output at a glance.

Run:
    python scripts/batch_inspect_playbyplay_fouls.py [--limit N] [--season-id SEASON]
Example:
    python scripts/batch_inspect_playbyplay_fouls.py --limit 150
    python scripts/batch_inspect_playbyplay_fouls.py --limit 50 --season-id 22024
"""

import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

from nba_api.stats.endpoints import playbyplayv3

SLEEP_SECONDS_BETWEEN_CALLS = 0.6
MAX_EXAMPLES_PER_SUBTYPE = 3

# Already hand-verified against real Sleeper scores (see
# architecture_risks.md #? tech-foul backfill investigation, 8/23/26).
CONFIRMED_COSTS_2 = {"Technical", "Flagrant Type 1", "Flagrant Type 2"}
CONFIRMED_NEEDS_PARSING = {"Double Technical"}  # costs -2 for BOTH named players, but personId only covers one
CONFIRMED_NO_PENALTY = {"Double Personal", "Defense 3 Second"}


def fetch_sample_game_ids(conn, limit, season_id=None):
    cur = conn.cursor()
    query = "SELECT DISTINCT game_id, season_id, game_date FROM game_logs"
    params = ()
    if season_id:
        query += " WHERE season_id = %s"
        params = (season_id,)
    query += " ORDER BY game_date LIMIT %s"
    cur.execute(query, params + (limit,))
    rows = cur.fetchall()
    cur.close()
    return rows


def classify(sub_type):
    if sub_type in CONFIRMED_COSTS_2:
        return "CONFIRMED: costs -2"
    if sub_type in CONFIRMED_NEEDS_PARSING:
        return "CONFIRMED: costs -2, needs description parsing (2 players)"
    if sub_type in CONFIRMED_NO_PENALTY:
        return "CONFIRMED: no penalty"
    return "*** UNCONFIRMED -- spot-check against a real Sleeper score before trusting ***"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100, help="Number of games to scan (default 100)")
    parser.add_argument("--season-id", default=None, help="e.g. 22024 -- omit to scan across all seasons")
    args = parser.parse_args()

    conn = get_connection()
    games = fetch_sample_game_ids(conn, args.limit, args.season_id)
    conn.close()

    print(f"Scanning {len(games)} game(s)" +
          (f" (season {args.season_id})" if args.season_id else " (all seasons)") + "...\n")

    # sub_type -> {"count": n, "games": set, "examples": [ (game_id, game_date, personId, playerName, description), ... ]}
    subtype_stats = defaultdict(lambda: {"count": 0, "games": set(), "examples": []})
    failures = []

    for i, (game_id, season_id, game_date) in enumerate(games, start=1):
        try:
            pbp = playbyplayv3.PlayByPlayV3(game_id=game_id)
            df = pbp.get_data_frames()[0]
            foul_like = df[df["actionType"].str.contains("foul", case=False, na=False)]
            for _, row in foul_like.iterrows():
                st = row["subType"]
                stats = subtype_stats[st]
                stats["count"] += 1
                stats["games"].add(game_id)
                if len(stats["examples"]) < MAX_EXAMPLES_PER_SUBTYPE:
                    stats["examples"].append((
                        game_id, game_date, row["personId"], row["playerName"], row["description"]
                    ))
        except Exception as e:
            failures.append((game_id, str(e)))

        if i % 10 == 0:
            print(f"  [{i}/{len(games)}] scanned...")

        time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

    print(f"\nDone. {len(games) - len(failures)}/{len(games)} games scanned successfully.\n")

    print("=== All distinct foul-related subTypes found ===\n")
    for st in sorted(subtype_stats.keys()):
        stats = subtype_stats[st]
        status = classify(st)
        print(f"subType={st!r}")
        print(f"  seen {stats['count']} time(s) across {len(stats['games'])} game(s)")
        print(f"  status: {status}")
        print(f"  example occurrences (game_id, game_date, personId, playerName, description):")
        for ex in stats["examples"]:
            print(f"    {ex}")
        print()

    if failures:
        print(f"\n{len(failures)} game(s) failed to fetch:")
        for game_id, err in failures:
            print(f"  {game_id}: {err}")


if __name__ == "__main__":
    main()
