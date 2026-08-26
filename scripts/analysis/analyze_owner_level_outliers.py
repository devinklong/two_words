"""
scripts/analysis/analyze_owner_level_outliers.py

Tests a gap the player-side outlier work never covered: is the team-
side "Center slot outscores everyone" finding partly an OWNER-skill
effect -- a specific roster_id consistently getting more out of their
C slot regardless of which player fills it, the same way a handful of
specific NBA players (Jokić et al.) turned out to be carrying the
player-side elite-Center premium?

Direct mirror of analyze_position_scoring_distributions.py's
outlier-robustness check, but excluding by roster_id instead of
nba_player_id: for each season, ranks owners by their average LOCKED
C-slot score, excludes the top_n highest, reruns C vs. Non-C
(Mann-Whitney) on what's left. If the Center-slot finding survives
excluding the top owners, it's a real, broad effect independent of any
one owner's skill -- consistent with the already-confirmed intrinsic
right-tail explanation (see analyze_selectivity_vs_intrinsic_
distribution.py). If it collapses, a specific owner's locking skill
was doing real work the intrinsic-distribution story alone wouldn't
explain.

Same Python-only convention as the rest of this suite -- single-pass
SQL, all math in numpy/scipy.
"""

import sys
from pathlib import Path
from collections import defaultdict

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
from scipy.stats import mannwhitneyu

MIN_GROUP_SIZE = 5
TOP_N_OWNERS = (1, 2, 3)

LOCKED_QUERY = """
    SELECT season, tier, slot, points, roster_id
    FROM locked_scores_by_slot
    WHERE slot IN ('PG', 'SG', 'SF', 'PF', 'C');
"""


def fetch_locked_rows(cur):
    cur.execute(LOCKED_QUERY)
    return cur.fetchall()


def build_owner_c_means(rows):
    """Each owner's average LOCKED C-slot score per season (pooled
    across tiers -- the question is whether an owner is broadly good at
    locking their Center, not tier-specific)."""
    scores_by_season_owner = defaultdict(list)
    for season, tier, slot, points, roster_id in rows:
        if slot != "C":
            continue
        scores_by_season_owner[(season, roster_id)].append(float(points))
    means_by_season = defaultdict(list)
    for (season, roster_id), scores in scores_by_season_owner.items():
        means_by_season[season].append((roster_id, np.mean(scores)))
    return means_by_season


def run_mannwhitney_c_vs_noncenter(rows, excluded_owners_by_season, label):
    print(f"\n=== Center vs. Non-Center, Mann-Whitney, {label} ===")
    buckets = defaultdict(list)
    for season, tier, slot, points, roster_id in rows:
        excluded = excluded_owners_by_season.get(season, set())
        if slot == "C" and roster_id in excluded:
            continue
        group = "C" if slot == "C" else "Non-C"
        buckets[(season, group)].append(float(points))

    by_season = defaultdict(dict)
    for (season, group), scores in buckets.items():
        by_season[season][group] = scores

    for season, groups in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        c_scores = groups.get("C", [])
        nc_scores = groups.get("Non-C", [])
        if len(c_scores) < MIN_GROUP_SIZE or len(nc_scores) < MIN_GROUP_SIZE:
            print(f"{season}: not enough C and/or Non-C games, skipped")
            continue
        stat, p = mannwhitneyu(c_scores, nc_scores, alternative="two-sided")
        n1, n2 = len(c_scores), len(nc_scores)
        prob_c_greater = stat / (n1 * n2)
        rank_biserial = 2 * prob_c_greater - 1
        verdict = "SIGNAL" if p < 0.05 else "noise (not significant)"
        print(
            f"{season}: {n1} C, {n2} Non-C  U={stat:.1f}  p={p:.4f}  "
            f"P(C>non-C)={prob_c_greater:.3f}  rank-biserial={rank_biserial:+.3f}  {verdict}"
        )


def run():
    conn = get_connection()
    cur = conn.cursor()
    rows = fetch_locked_rows(cur)
    cur.close()
    conn.close()

    print(f"{len(rows)} locked (dedicated-position) rows pulled.")

    owner_c_means = build_owner_c_means(rows)

    # Baseline: no exclusion, for direct comparison against each top_n pass.
    run_mannwhitney_c_vs_noncenter(rows, {}, "baseline, no owners excluded")

    for n in TOP_N_OWNERS:
        excluded_by_season = {}
        for season, owner_means in owner_c_means.items():
            top_owners = sorted(owner_means, key=lambda x: -x[1])[:n]
            excluded_by_season[season] = {roster_id for roster_id, _ in top_owners}
            print(f"\n{season}: excluding top {n} owner(s) by avg locked C-slot score -- roster_id(s) {sorted(excluded_by_season[season])}")
        run_mannwhitney_c_vs_noncenter(rows, excluded_by_season, f"top {n} owner(s) excluded")


if __name__ == "__main__":
    run()
