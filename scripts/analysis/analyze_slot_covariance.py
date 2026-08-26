"""
scripts/analysis/analyze_slot_covariance.py

Fantasy-SLOT covariance, deliberately scoped narrow -- NOT NBA-team
stacking (a real, different question, scoped separately, not built
here -- see docs note). A team's weekly total is a SUM of up to 9 slot
scores; this asks whether those slots move together (a good week means
everything spikes at once) or independently (each slot's outcome is
close to unrelated to the others), using only fantasy roster slots
already in locked_scores_by_slot -- no NBA-team/game data involved at
all.

Pivots to one row per (league_id, week, roster_id), one column per
slot, computes the correlation matrix, and decomposes total team-week
variance into the sum of each slot's own variance vs. the real
(possibly correlated) total variance -- if slots were independent,
those two numbers would match; a gap shows real covariance either
inflating or dampening total team risk.

REAL CONFOUND FOUND AND CORRECTED (8/25/26): the first version pooled
every roster together and found strong positive correlation across
every slot pair. That result was confounded, not a real per-week
effect -- some fantasy managers just draft/manage a stronger roster
than others, so a team with a strong C also tends to have a strong PG,
not because those two specific players' games are linked, but because
they're often the same team's players and that team is just better
overall. Pooling rosters together manufactures correlation from
between-team skill differences, not real within-week co-movement.

Fix: every slot is DEMEANED by its own (league_id, roster_id) group
mean before computing correlation/variance -- this removes "some teams
are just better" entirely and tests the real question instead: once
team quality is held constant, does THIS team's C and PG still tend to
spike in the SAME week more than chance predicts? Both the raw
(pre-fix) and demeaned (corrected) results are printed side by side so
the size of the confound itself is visible, not just the corrected
number.

Same Python-only convention as the rest of this suite -- single-pass
SQL, all math in pandas/numpy.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
import pandas as pd

DEDICATED_SLOTS = ["PG", "SG", "SF", "PF", "C"]

LOCKED_QUERY = """
    SELECT league_id, week, roster_id, slot, points
    FROM locked_scores_by_slot
    WHERE slot IN ('PG', 'SG', 'SF', 'PF', 'C');
"""


def fetch_locked_rows(cur):
    cur.execute(LOCKED_QUERY)
    return cur.fetchall()


def build_wide_frame(rows):
    """One row per (league_id, week, roster_id), one column per slot.
    Team-weeks missing any dedicated slot (BYE/injury/etc.) are dropped
    for the correlation/variance-decomposition math, which need
    complete rows -- printed as a count, not silently absorbed."""
    df = pd.DataFrame(rows, columns=["league_id", "week", "roster_id", "slot", "points"])
    df["points"] = df["points"].astype(float)
    wide = df.pivot_table(index=["league_id", "week", "roster_id"], columns="slot", values="points")
    complete = wide.dropna()
    return wide, complete


def demean_by_roster(complete):
    """Subtracts each (league_id, roster_id)'s own mean from every slot
    column -- removes real between-team skill differences, so what's
    left is purely within-team, week-to-week co-movement."""
    return complete.groupby(level=["league_id", "roster_id"]).transform(lambda col: col - col.mean())


def run_correlation_matrix(data, label):
    print(f"\n=== Correlation matrix across dedicated slots, {label} ({len(data)} team-weeks) ===")
    corr = data[DEDICATED_SLOTS].corr()
    header = f"{'':<6}" + "".join(f"{s:>8}" for s in DEDICATED_SLOTS)
    print(header)
    for slot in DEDICATED_SLOTS:
        row = f"{slot:<6}" + "".join(f"{corr.loc[slot, s]:>8.3f}" for s in DEDICATED_SLOTS)
        print(row)


def run_variance_decomposition(data, label):
    """Sum of each slot's own variance vs. the real variance of the
    team-week TOTAL (sum across slots). Equal = independent slots. Real
    total variance HIGHER than the sum = positive covariance (slots
    tend to spike together, amplifying team-level swings). LOWER = slots
    partially offset each other, damping total variance below what
    independence would predict."""
    print(f"\n=== Variance decomposition, {label} ===")
    individual_var_sum = data[DEDICATED_SLOTS].var(ddof=1).sum()
    team_total = data[DEDICATED_SLOTS].sum(axis=1)
    real_total_var = team_total.var(ddof=1)
    diff_pct = 100.0 * (real_total_var - individual_var_sum) / individual_var_sum
    print(f"Sum of individual slot variances: {individual_var_sum:.2f}")
    print(f"Real variance of team-week total: {real_total_var:.2f}")
    direction = "slots spike together (amplifies team risk)" if diff_pct > 0 else "slots partially offset (dampens team risk)"
    print(f"Difference: {diff_pct:+.1f}% -- {direction}" if abs(diff_pct) > 2 else f"Difference: {diff_pct:+.1f}% -- effectively independent")


def run():
    conn = get_connection()
    cur = conn.cursor()
    rows = fetch_locked_rows(cur)
    cur.close()
    conn.close()

    print(f"{len(rows)} locked (dedicated-slot) rows pulled.")

    wide, complete = build_wide_frame(rows)
    dropped = len(wide) - len(complete)
    print(f"{len(wide)} total team-weeks, {len(complete)} with all 5 dedicated slots filled ({dropped} dropped for incomplete data).")

    if len(complete) < 10:
        print("Not enough complete team-weeks to compute a meaningful correlation/variance result.")
        return

    run_correlation_matrix(complete, "RAW -- confounded by real between-team skill differences")
    run_variance_decomposition(complete, "RAW -- confounded by real between-team skill differences")

    demeaned = demean_by_roster(complete)
    run_correlation_matrix(demeaned, "WITHIN-TEAM DEMEANED -- corrected, real week-to-week co-movement only")
    run_variance_decomposition(demeaned, "WITHIN-TEAM DEMEANED -- corrected, real week-to-week co-movement only")


if __name__ == "__main__":
    run()
