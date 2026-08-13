"""
tests/test_advanced_stats_vs_basketball_reference.py

`team_game_advanced_stats` computes ONE possessions formula (simplified:
FGA - OREB + TOV + 0.4*FTA, averaged both teams, no full Basketball-
Reference rebounding-rate-weighted alternative -- that was never built).
So this isn't an internal formula-vs-formula diff. It's exactly what the
view's own header comment calls for: cross-check pace/off_rating/
def_rating against a real published source before trusting the formula
for anything beyond exploration.

IMPORTANT CAVEAT (read before adding more games): the view's `pace`
column is NOT normalized to 48 minutes -- it's the raw possession
estimate. Basketball-Reference's own ORtg/DRtg are computed against that
same kind of raw (non-normalized) possession estimate, so those SHOULD
line up closely. But BBRef's separately-published "Pace" stat IS
normalized to 48 minutes, so it will NOT match this view's `pace` column
directly -- especially on OT games, where 48-minute normalization vs.
raw possessions diverge the most. Don't treat a pace mismatch as a bug
unless you've also renormalized one side.

This only validates off_rating / def_rating / net_rating against BBRef.
Pace is printed for inspection but not asserted against BBRef's Pace stat,
since that's a genuinely different (normalized) quantity.

Add more KNOWN_GAMES entries as you spot-check more games -- non-OT
games are the more useful additions, since they remove the normalization
question entirely (pace SHOULD match BBRef's Pace stat too when the game
is exactly 48 minutes / 240 team-minutes).
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]/ "scripts"))  # project root (tests/ is one level down) # project root, matches existing test-suite convention
from db_connection import get_connection

TOLERANCE = {
    "off_rating": 2.0,   # points per 100 possessions
    "def_rating": 2.0,
    "net_rating": 3.0,
}

# Values taken directly from each game's Basketball-Reference box score
# (Team Totals row, Advanced Box Score Stats table).
KNOWN_GAMES = [
    {
        "game_id": "0022400909",
        "date": "2025-03-07",
        "note": "DEN 149, PHX 141 (OT) -- pace comparison N/A, see module docstring",
        "teams": {
            "PHX": {"pts": 141, "off_rating": 132.9, "def_rating": 140.5},
            "DEN": {"pts": 149, "off_rating": 140.5, "def_rating": 132.9},
        },
    },
    # Add non-OT games here for cleaner pace validation, e.g.:
    # {
    #     "game_id": "00224XXXXX",
    #     "date": "YYYY-MM-DD",
    #     "note": "TEAM1 ### , TEAM2 ### (regulation)",
    #     "teams": {
    #         "TM1": {"pts": ..., "off_rating": ..., "def_rating": ...},
    #         "TM2": {"pts": ..., "off_rating": ..., "def_rating": ...},
    #     },
    # },
]


def fetch_game_rows(cur, game_id):
    """Pull both teams' rows for a game, joined to `teams` for the abbreviation/name
    so we can match against BBRef's team labels."""
    query = """
        SELECT tgas.game_id, tgas.team_id, tgas.is_home, tgas.pts, tgas.opp_pts,
               tgas.team_possessions_est, tgas.opp_possessions_est, tgas.pace,
               tgas.off_rating, tgas.def_rating, tgas.net_rating,
               t.full_name AS team_name
        FROM team_game_advanced_stats tgas
        JOIN teams t ON t.team_id = tgas.team_id
        WHERE tgas.game_id = %s
        ORDER BY tgas.is_home DESC;
    """
    cur.execute(query, (game_id,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def match_row_to_expected(db_row, expected_by_abbrev):
    """Match a DB row to its expected BBRef values by points scored (safer than
    guessing abbreviation-from-full_name formatting)."""
    for abbrev, exp in expected_by_abbrev.items():
        if db_row["pts"] == exp["pts"]:
            return abbrev, exp
    return None, None


def run():
    conn = get_connection()
    cur = conn.cursor()

    total_checks = 0
    total_flagged = 0

    for game in KNOWN_GAMES:
        print("=" * 70)
        print(f"GAME {game['game_id']}  ({game['date']})  -- {game['note']}")
        print("=" * 70)

        rows = fetch_game_rows(cur, game["game_id"])
        if not rows:
            print("  No rows found in team_game_advanced_stats for this game_id.")
            print("  (Check that team_game_stats/the view have been populated for this date.)")
            continue

        for db_row in rows:
            abbrev, expected = match_row_to_expected(db_row, game["teams"])
            label = f"{db_row['team_name']} ({abbrev or '???'})"
            print(f"\n  {label}")
            print(f"    pts={db_row['pts']}  opp_pts={db_row['opp_pts']}  "
                  f"pace(raw, unnormalized)={db_row['pace']}")

            if expected is None:
                print("    No matching expected row (points didn't match any KNOWN_GAMES entry) "
                      "-- skipping assertion.")
                continue

            for metric in ("off_rating", "def_rating"):
                actual = float(db_row[metric])
                exp_val = expected[metric]
                diff = abs(actual - exp_val)
                total_checks += 1
                status = "OK"
                if diff > TOLERANCE[metric]:
                    status = "FLAGGED"
                    total_flagged += 1
                print(f"    {metric:12s} computed={actual:7.2f}  bbref={exp_val:7.2f}  "
                      f"diff={diff:5.2f}  [{status}]")

            net_actual = float(db_row["net_rating"])
            net_expected = expected["off_rating"] - expected["def_rating"]
            net_diff = abs(net_actual - net_expected)
            total_checks += 1
            status = "OK"
            if net_diff > TOLERANCE["net_rating"]:
                status = "FLAGGED"
                total_flagged += 1
            print(f"    {'net_rating':12s} computed={net_actual:7.2f}  bbref={net_expected:7.2f}  "
                  f"diff={net_diff:5.2f}  [{status}]")

    cur.close()
    conn.close()

    print()
    print("=" * 70)
    if total_checks == 0:
        print("RESULT: no games validated -- populate KNOWN_GAMES or check that the "
              "relevant game_ids exist in team_game_advanced_stats.")
    elif total_flagged == 0:
        print(f"RESULT: {total_checks}/{total_checks} checks within tolerance. "
              "The simplified formula tracks BBRef's ORtg/DRtg closely enough to trust "
              "for the rolling aggregation step.")
    else:
        print(f"RESULT: {total_flagged}/{total_checks} checks exceeded tolerance -- "
              "look at which metric/game before moving to step 3.")
    print("=" * 70)


if __name__ == "__main__":
    run()
