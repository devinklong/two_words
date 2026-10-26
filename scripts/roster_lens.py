"""Roster lens for the lock report (added 10/25/26): is a score worth locking given what YOUR OTHER players can still do?

The player-only signal asks "is this score high for HIM?" The roster lens asks "is this score high for the SLOT it would fill?".
For an open slot, it looks at every unlocked, eligible player on your roster and the games each still has this week, simulates
those scores from each player's own average and standard deviation, and works backward day by day: if you wait, what is the slot
worth on average, assuming you will always lock the best score you see (and are forced to lock on the last day)? That number is the
slot's bar: lock a score at or above it, hold below it. A week with many strong players and many games left raises the bar;
a thin bench or few games left lowers it.
Known simplifications: players are treated as independent across slots (other open slots are filled first, scarcest slot first, so a player is not counted for two slots); scores are normal draws cut off at 0; no injuries, minutes or back-to-back effects; the "behind in
the week, so push past the usual bar" adjustment is NOT built yet. Pure functions, no database access.

Self-test:  python scripts/roster_lens.py
"""
from collections import defaultdict

import numpy as np

from lineup_fit import eligible

N_SIMS = 4000


def slot_threshold(alts, day, n=N_SIMS, seed=11):
    """alts: [(avg, sd, [game dates])]. Expected value of waiting, from the games after `day`."""
    rng = np.random.default_rng(seed)
    by_date = defaultdict(list)
    for avg, sd, dates in alts:
        for d in dates:
            if d > day:
                by_date[d].append((avg, sd if sd and sd > 0 else 0.3 * avg))
    if not by_date:
        return 0.0
    value = None
    for d in sorted(by_date, reverse=True):          # last game day first
        best = None
        for avg, sd in by_date[d]:
            draw = np.clip(rng.normal(avg, sd, n), 0, None)
            best = draw if best is None else np.maximum(best, draw)
        value = best.mean() if value is None else np.maximum(best, value).mean()
    return float(value)


def open_slot_list(all_slots, filled):
    """The slots still open: all_slots minus one occurrence per filled label."""
    left = list(all_slots)
    for f in filled:
        if f in left:
            left.remove(f)
    return left


def expected_best(avg, sd, dates, day, n=2000, seed=5):
    """Average of a player's best score over his remaining games (how much he is worth to a slot)."""
    k = sum(1 for d in dates if d > day)
    if k == 0:
        return 0.0
    rng = np.random.default_rng(seed)
    return float(np.clip(rng.normal(avg, sd if sd and sd > 0 else 0.3 * avg, (n, k)), 0, None).max(axis=1).mean())


def apply_lens(rows, alts, open_slots, day):
    """rows: dicts with score, positions, slot. alts: dicts with positions, avg, sd, dates (unlocked roster players).
    Sets r['lens'] = {'call','slot','bar','margin'} or None for rows already locked / with no open slot he fits."""
    cache = {}
    for a in alts:
        a["worth"] = expected_best(a["avg"], a["sd"], a["dates"], day)

    def bar_for(slot):
        # If this slot stays open, what is left for it? Fill the OTHER open slots first (scarcest slot first, each taking the
        # best-worth eligible player still available); this slot's pool is whoever remains and fits it.
        if slot not in cache:
            others = list(open_slots)
            others.remove(slot)
            avail = sorted(alts, key=lambda a: -a["worth"])
            for o in sorted(others, key=lambda o: sum(1 for a in avail if eligible(a["positions"], o))):
                for a in avail:
                    if eligible(a["positions"], o):
                        avail.remove(a)
                        break
            pool = [a for a in avail if eligible(a["positions"], slot)]
            cache[slot] = slot_threshold([(a["avg"], a["sd"], a["dates"]) for a in pool], day)
        return cache[slot]

    for r in rows:
        r["lens"] = None
        if str(r.get("slot") or "").startswith("locked"):
            continue
        fits = sorted({s for s in open_slots if eligible(r["positions"], s)})
        if not fits:
            continue
        slot = min(fits, key=bar_for)                # the open slot where this score has the best chance
        bar = bar_for(slot)
        r["lens"] = {"call": "LOCK" if r["score"] >= bar else "HOLD", "slot": slot, "bar": bar, "margin": r["score"] - bar}
    return rows


if __name__ == "__main__":
    from datetime import date, timedelta
    d0 = date(2026, 10, 24)
    days = [d0 + timedelta(days=i) for i in range(1, 4)]
    strong = [(52.0, 12.0, days)] * 3                      # three strong players, 3 games left each
    weak = [(35.0, 9.0, days[:1])]                         # one mid player, 1 game left
    hi, lo = slot_threshold(strong, d0), slot_threshold(weak, d0)
    assert hi > 55 and lo < 45 and hi > lo, (hi, lo)
    assert slot_threshold(strong, days[-1]) == 0.0         # nothing left after the last day: any score locks
    rows = [{"score": 39.4, "positions": ["SG"], "slot": ""}, {"score": 34.3, "positions": ["SG"], "slot": ""},
            {"score": 70.0, "positions": ["SG"], "slot": "locked SG"}]
    alts = [{"positions": ["SG"], "avg": a, "sd": s, "dates": dd} for a, s, dd in strong]
    apply_lens(rows, alts, ["SG"], d0)                       # one open slot, three strong players left: wait
    assert rows[0]["lens"]["call"] == "HOLD" and rows[2]["lens"] is None
    bar_one = rows[0]["lens"]["bar"]
    apply_lens(rows, alts, ["SG", "UTIL", "UTIL"], d0)       # three open slots share the same three players: lower bar
    assert rows[0]["lens"]["bar"] < bar_one - 5, (rows[0]["lens"]["bar"], bar_one)
    alts_weak = [{"positions": ["SG"], "avg": a, "sd": s, "dates": dd} for a, s, dd in weak]
    apply_lens(rows, alts_weak, ["SG"], d0)
    # thin bench: 39.4 clears the slot's bar (~35), 34.3 just misses it -> same player-level quality, different roster call
    assert rows[0]["lens"]["call"] == "LOCK" and rows[1]["lens"]["call"] == "HOLD", [r["lens"] for r in rows]
    print(f"roster_lens self-test passed (strong bench bar {hi:.1f}, thin bench bar {lo:.1f})")
