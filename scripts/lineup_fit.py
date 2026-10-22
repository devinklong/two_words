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


SPECIFIC_SLOTS = ["PG", "SG", "SF", "PF", "C"]   # tier 1: always preferred
FLEX_SLOTS = ["G", "F"]                          # tier 2
# tier 3: UTIL


def slot_supply(candidates):
    """candidates: list of (positions, games_left). Returns {slot: total remaining games among
    players eligible for that slot} for every distinct slot."""
    supply = {}
    for slot in dict.fromkeys(SLOTS):
        supply[slot] = sum(g for positions, g in candidates if eligible(positions, slot))
    return supply


def recommend_lock_slots(lockers, supply, filled=()):
    """Which lineup slot should each LOCK player be locked into?
    lockers: list of dicts {"name", "positions", "gap"}; processed biggest gap first.
    supply:  {slot: remaining games among the OTHER players eligible for it} (see slot_supply).
    filled:  slots already used this week (a slot can appear twice, e.g. UTIL).
    Rule: use the most specific tier that has an open eligible slot (PG/SG/SF/PF/C, then G/F,
    then UTIL). Within a tier take the slot with the LEAST remaining supply, because it is the
    hardest to fill later; ties go in SLOTS order. Returns {name: (slot, reason)}.
    A player with nothing open and eligible gets (None, reason)."""
    open_slots = list(SLOTS)
    for f in filled:
        if f in open_slots:
            open_slots.remove(f)
    out = {}
    for pl in sorted(lockers, key=lambda x: -x.get("gap", 0)):
        chosen, reason = None, "no open slot he is eligible for"
        for tier in (SPECIFIC_SLOTS, FLEX_SLOTS, ["UTIL"]):
            options = [sl for sl in dict.fromkeys(open_slots) if sl in tier and eligible(pl["positions"], sl)]
            if not options:
                continue
            chosen = min(options, key=lambda sl: (supply.get(sl, 0), SLOTS.index(sl)))
            if len(options) > 1:
                reason = ", ".join(f"{sl} {supply.get(sl, 0):g}" for sl in options) + " games left -> scarcest"
            elif tier == SPECIFIC_SLOTS:
                reason = f"only open position slot he fits ({chosen} {supply.get(chosen, 0):g} games left)"
            else:
                reason = f"no open position slot; {chosen}"
            break
        if chosen:
            open_slots.remove(chosen)
        out[pl["name"]] = (chosen, reason)
    return out


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
    # slot recommendation: PG-eligible games abound, SG games are scarce -> a PG/SG player goes to SG
    cands = [(["PG"], 3), (["PG"], 2), (["PG", "SG"], 2), (["SG"], 1)]
    sup = slot_supply(cands)
    print("supply:", sup)
    print(recommend_lock_slots([{"name": "Q", "positions": ["PG", "SG"], "gap": 8}], sup))
    print("SG filled:", recommend_lock_slots([{"name": "Q", "positions": ["PG", "SG"], "gap": 8}], sup, filled=["SG"]))
