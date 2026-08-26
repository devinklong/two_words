"""
scripts/analysis/analyze_eligibility_vs_locked_outcomes.py

Links two findings that were never actually connected: Part 2
(player-side) found that single-position-eligible players show a real
elite-tier scoring edge in their RAW per-game distribution, surviving
outlier exclusion (see v3_2_position_scoring_investigation.md). Part 3
(team-side) works entirely from real LOCKED decisions instead. Nobody
has checked whether the Part 2 eligibility finding actually shows up
in Part 3's locked outcomes too, or whether it's specific to the raw,
unfiltered player-side data.

Buckets real locked scores (locked_scores_by_slot) by each locked
player's position-eligibility count (1 / 2 / 3+, from
sleeper_player_fantasy_positions) and tests for a difference the same
way Part 2 did -- Kruskal-Wallis + effect size, at the elite tier
specifically (where Part 2's effect was found) and pooled across tiers
for comparison.

Same Python-only convention as the rest of this suite -- single-pass
SQL, all math in numpy/scipy.
"""

import sys
from pathlib import Path
from collections import defaultdict

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
from scipy.stats import kruskal

MIN_GROUP_SIZE = 5

LOCKED_QUERY = """
    SELECT season, tier, points, nba_player_id
    FROM locked_scores_by_slot
    WHERE slot IN ('PG', 'SG', 'SF', 'PF', 'C')
      AND nba_player_id IS NOT NULL;
"""

ELIGIBILITY_QUERY = """
    SELECT spc.nba_player_id, COUNT(*) AS n_positions
    FROM sleeper_player_crosswalk spc
    JOIN sleeper_player_fantasy_positions spfp
        ON spfp.sleeper_player_id = spc.sleeper_player_id
    GROUP BY spc.nba_player_id;
"""


def fetch_locked_rows(cur):
    cur.execute(LOCKED_QUERY)
    return cur.fetchall()


def fetch_eligibility_counts(cur):
    cur.execute(ELIGIBILITY_QUERY)
    return {nba_player_id: n_positions for nba_player_id, n_positions in cur.fetchall()}


def eligibility_bucket(n_positions):
    if n_positions <= 1:
        return "1 position"
    if n_positions == 2:
        return "2 positions"
    return "3+ positions"


def group_locked_by_eligibility(rows, eligibility_counts):
    buckets = defaultdict(list)
    for season, tier, points, nba_player_id in rows:
        n_positions = eligibility_counts.get(nba_player_id)
        if n_positions is None:
            continue
        bucket = eligibility_bucket(n_positions)
        buckets[(season, tier, bucket)].append(float(points))
    return buckets


def kruskal_with_effect_size(groups):
    stat, p = kruskal(*groups)
    k = len(groups)
    n = sum(len(g) for g in groups)
    epsilon_sq = (stat - k + 1) / (n - k) if n > k else float("nan")
    return stat, p, epsilon_sq


def effect_label(eps):
    if np.isnan(eps):
        return "n/a"
    if eps < 0.01:
        return "negligible"
    if eps < 0.06:
        return "small"
    if eps < 0.14:
        return "medium"
    return "large"


def print_percentile_table(buckets):
    print("\n=== Percentile table, locked scores by eligibility count (season, tier, bucket) ===")
    header = f"{'season':<8}{'tier':<10}{'bucket':<14}{'n':>5}{'mean':>8}{'stdev':>8}"
    print(header)
    for (season, tier, bucket), scores in sorted(buckets.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), kv[0][2])):
        if len(scores) < MIN_GROUP_SIZE:
            continue
        arr = np.array(scores)
        print(f"{str(season):<8}{str(tier):<10}{bucket:<14}{len(arr):>5}{arr.mean():>8.2f}{arr.std(ddof=1):>8.2f}")


def run_kruskal_by_tier(buckets, tier_filter, label):
    print(f"\n=== Kruskal-Wallis, locked scores by eligibility count, {label} ===")
    by_season = defaultdict(lambda: defaultdict(list))
    for (season, tier, bucket), scores in buckets.items():
        if tier_filter is not None and tier != tier_filter:
            continue
        by_season[season][bucket].extend(scores)

    for season, bucket_groups in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        groups = {b: s for b, s in bucket_groups.items() if len(s) >= MIN_GROUP_SIZE}
        if len(groups) < 2:
            print(f"{season}: not enough eligibility groups with >={MIN_GROUP_SIZE} scores, skipped")
            continue
        stat, p, eps = kruskal_with_effect_size(list(groups.values()))
        verdict = "SIGNAL" if p < 0.05 else "noise (not significant)"
        print(f"{season}: H={stat:.2f}  p={p:.4f}  eps2={eps:.4f} ({effect_label(eps)})  {verdict}")


def run():
    conn = get_connection()
    cur = conn.cursor()
    rows = fetch_locked_rows(cur)
    eligibility_counts = fetch_eligibility_counts(cur)
    cur.close()
    conn.close()

    print(f"{len(rows)} locked (dedicated-position) rows pulled.")
    print(f"{len(eligibility_counts)} players' eligibility counts pulled.")

    buckets = group_locked_by_eligibility(rows, eligibility_counts)
    print_percentile_table(buckets)

    run_kruskal_by_tier(buckets, tier_filter="1_elite", label="elite tier only (matches Part 2's finding)")
    run_kruskal_by_tier(buckets, tier_filter=None, label="pooled across all tiers")


if __name__ == "__main__":
    run()
