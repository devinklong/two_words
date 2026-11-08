"""Unit tests for scripts/lineup_fit.py with answers worked out by hand. No database needed.

Run:  python tests/test_lineup_fit.py        (exit 0 = all pass; golden_check.py runs it too)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lineup_fit import best_lineup_value, eligible, lineup_gain, recommend_lock_slots, slot_supply

FULL = [(50, ["PG"]), (48, ["SG"]), (47, ["PG", "SG"]), (46, ["SF"]), (45, ["PF"]), (44, ["SF", "PF"]),
        (60, ["C"]), (40, ["C"]), (39, ["C"])]


def test_eligibility():
    assert eligible(["PG"], "G") and eligible(["SG"], "G") and not eligible(["SF"], "G")
    assert eligible(["PF"], "F") and eligible(["SF"], "F") and not eligible(["C"], "F")
    assert eligible([], "UTIL") and not eligible([], "PG")


def test_best_lineup_value():
    # C 60, PG 50, SG 48, G 47, SF 46, PF 45, F 44, UTIL 40 and 39
    assert best_lineup_value(FULL) == 419
    assert best_lineup_value([]) == 0
    assert best_lineup_value([(50, ["PG"]), (48, ["SG"])]) == 98   # two players, the rest empty


def test_lineup_gain():
    assert lineup_gain(FULL, (38, ["PG"])) == 0       # cannot beat the weakest starter (39 in UTIL)
    assert lineup_gain(FULL, (52, ["C"])) == 13       # replaces the 39 in UTIL
    assert lineup_gain([(50, ["PG"]), (48, ["SG"])], (45, ["C"])) == 45   # a thin roster gets the full value in an empty slot


def test_lock_slots():
    sup = slot_supply([(["PG"], 3), (["PG"], 2), (["PG", "SG"], 2), (["SG"], 1)])
    assert sup["PG"] == 7 and sup["SG"] == 3 and sup["C"] == 0
    lockers = [{"name": "Q", "positions": ["PG", "SG"], "gap": 8}]
    assert recommend_lock_slots(lockers, sup)["Q"][0] == "SG"                      # SG games are scarcer, so lock into SG
    assert recommend_lock_slots(lockers, sup, filled=["SG"])["Q"][0] == "PG"       # SG already used


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}  {e!r}")
    sys.exit(1 if failed else 0)
