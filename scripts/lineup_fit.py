"""
scripts/lineup_fit.py

Best-lineup math for the league's starting slots, used by waiver_bid_advisor.py
to measure how much a free agent would improve a team. Added 10/21/26.
Pure functions, no database access.

League slots (sleeper_leagues.roster_positions): PG, SG, G, SF, PF, F, C, UTIL, UTIL
(+ 6 bench, which do not score). Eligibility:
  PG/SG/SF/PF/C  the player's fantasy positions include it
  G              includes PG, SG or G
  F              includes SF, PF or F
  UTIL           anyone
A player with no known position can only fill UTIL.

best_lineup_value() picks the assignment of players to the 9 slots that maximizes the
sum of their values (a small assignment problem, solved exactly with scipy's
linear_sum_assignment). lineup_gain() is the value of the best lineup WITH a
candidate minus WITHOUT him: how many fantasy points per game he adds to that team.
A slot with nobody eligible contributes 0.

Run the self-test:  python scripts/lineup_fit.py
"""

SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL", "UTIL"]
G_SET = {"PG", "SG", "G"}
F_SET = {"SF", "PF", "F"}
KNOWN_POSITIONS = {"PG", "SG", "G", "SF", "PF", "F", "C"}
_INELIGIBLE = 1e6


def eligible(positions, slot):
    pos = set(positions or [])
    if slot == "UTIL":
        return True
    if slot == "G":
        return bool(pos & G_SET)
    if slot == "F":
        return bool(pos & F_SET)
    return slot in pos


def best_lineup_value(players, slots=SLOTS):
    """players: list of (value, positions). Returns the best total value over the slots."""
    import numpy as np
    from scipy.optimize import linear_sum_assignment

    if not players:
        return 0.0
    cost = np.empty((len(slots), len(players)))
    for i, slot in enumerate(slots):
        for j, (value, positions) in enumerate(players):
            cost[i, j] = -value if eligible(positions, slot) else _INELIGIBLE
    rows, cols = linear_sum_assignment(cost)
    return float(sum(players[j][0] for i, j in zip(rows, cols) if cost[i, j] < _INELIGIBLE / 2))


def lineup_gain(roster, candidate, base_value=None):
    """Points per game the candidate adds to this roster's best lineup (never negative)."""
    base = best_lineup_value(roster) if base_value is None else base_value
    return best_lineup_value(roster + [candidate]) - base


if __name__ == "__main__":
    # tiny self-test
    assert eligible(["PG"], "G") and eligible(["SG"], "G") and not eligible(["SF"], "G")
    assert eligible(["PF"], "F") and eligible(["SF"], "F") and not eligible(["C"], "F")
    assert eligible([], "UTIL") and not eligible([], "PG")
    full = [(50, ["PG"]), (48, ["SG"]), (47, ["PG", "SG"]), (46, ["SF"]), (45, ["PF"]), (44, ["SF", "PF"]),
            (60, ["C"]), (40, ["C"]), (39, ["C"])]
    base = best_lineup_value(full)
    print("base lineup value:", base)
    # a 38-point guard cannot beat the weakest starter anywhere: gain 0
    print("weak guard gain:", lineup_gain(full, (38, ["PG"])))
    # a 52-point center replaces a UTIL (39) -> +13
    print("strong C gain:  ", lineup_gain(full, (52, ["C"])))
    # a thin roster (no C at all) gets the full value in an empty slot
    thin = [(50, ["PG"]), (48, ["SG"])]
    print("thin roster, 45 C gain:", lineup_gain(thin, (45, ["C"])))
    print("empty roster:", best_lineup_value([]))
