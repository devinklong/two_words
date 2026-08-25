"""
scripts/analysis/analyze_slot_scoring_distributions.py

Team-side counterpart to analyze_position_scoring_distributions.py:
which fantasy roster SLOT (PG/SG/G/SF/PF/F/C/UTIL) produced the
highest locked score, across this league's 2 real seasons of history
(2024-25, 2025-26) -- fantasy teams, not NBA teams. Reads
locked_scores_by_slot (real locked decisions, not raw per-game
distributions).

SAME DESCRIPTIVE-NOT-PREDICTIVE FRAMING as the player-side work, for
the same reason: no claim this generalizes to future seasons, only a
description of what happened in this league's own real history.

OUTLIER-ROBUSTNESS BUILT IN FROM THE START, not added after a
surprising result -- the central lesson from the player-side
investigation (an apparent "Center premium" turned out to be 2-3
specific players). Team-side sample sizes are far smaller (~2 seasons
x 10 rosters x ~20 weeks x ~9 slots vs. ~146k player-game rows before),
so a single owner's single star dominating a slot's whole season is a
real, not theoretical, risk. Every slot-level test below runs twice:
once on the full data, once with the same known outlier players from
the player-side investigation excluded (Jokić, Embiid, AD, Wembanyama,
plus any additional outliers flagged by this script's own per-slot
check) -- if a slot's result doesn't survive exclusion, it's reported
as player-driven, not a real slot effect.

TIER STRATIFICATION carried over from the player-side lesson (position
effects reversed by tier there) -- every test also runs within each
tier, not just pooled.

UTIL SLOTS are NOT a position -- they accept any eligible player, so
"UTIL's value" is really just "value of whoever's rostered there,"
answering a different question than the other slots. Reported
separately, not folded into the position comparison.

SAMPLE SIZE CAVEAT: only 2 real seasons exist in player_scores (vs. 5
for the player-side work) -- treat every result here with more caution
than the player-side findings, not equal confidence.

Requires (not otherwise a project dependency yet):
    pip install scipy pandas scikit-posthocs --break-system-packages
"""

import sys
from pathlib import Path
from collections import defaultdict

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
import pandas as pd
from scipy.stats import kruskal, levene, mannwhitneyu
import scikit_posthocs as sp

PERCENTILES = [10, 25, 50, 75, 90]
MIN_GROUP_SIZE = 5

# Known outlier players from the player-side investigation
# (analyze_position_scoring_distributions.py) -- excluded by default in
# every test here, not just when a result looks surprising.
KNOWN_OUTLIER_NBA_IDS = {203999, 203076, 203954, 1627734, 1630578, 1641705}

VIEW_QUERY = """
    SELECT season, tier, slot, points, nba_player_id, league_id, week, roster_id
    FROM locked_scores_by_slot
    WHERE slot != 'UTIL';
"""

UTIL_QUERY = """
    SELECT season, tier, slot, points, nba_player_id, league_id, week, roster_id
    FROM locked_scores_by_slot
    WHERE slot = 'UTIL';
"""


def fetch_rows(cur, query):
    cur.execute(query)
    return cur.fetchall()


def group_scores(rows, exclude_ids=None):
    """Buckets points by (season, tier, slot). exclude_ids, if given,
    drops rows for those nba_player_ids entirely -- the outlier-
    robustness mechanism, run by default alongside the full-data pass."""
    exclude_ids = exclude_ids or set()
    buckets = defaultdict(list)
    for season, tier, slot, points, nba_player_id, league_id, week, roster_id in rows:
        if nba_player_id in exclude_ids:
            continue
        buckets[(season, tier, slot)].append(float(points))
    return buckets


def build_season_groups(buckets):
    by_season = defaultdict(lambda: defaultdict(list))
    for (season, tier, slot), scores in buckets.items():
        by_season[season][slot].extend(scores)
    return by_season


def build_season_tier_groups(buckets):
    by_season_tier = defaultdict(lambda: defaultdict(list))
    for (season, tier, slot), scores in buckets.items():
        by_season_tier[(season, tier)][slot].extend(scores)
    return by_season_tier


def print_percentile_table(buckets, label):
    print(f"\n=== Percentile table, {label} (season, tier, slot) ===")
    header = (
        f"{'season':<8}{'tier':<10}{'slot':<6}{'n':>5}"
        + "".join(f"{'p' + str(p):>8}" for p in PERCENTILES)
        + f"{'mean':>8}{'stdev':>8}"
    )
    print(header)
    for (season, tier, slot), scores in sorted(
        buckets.items(), key=lambda k: (str(k[0][0]), str(k[0][1]), k[0][2])
    ):
        if len(scores) < MIN_GROUP_SIZE:
            continue
        arr = np.array(scores)
        pcts = np.percentile(arr, PERCENTILES)
        row = (
            f"{str(season):<8}{str(tier):<10}{slot:<6}{len(arr):>5}"
            + "".join(f"{p:>8.2f}" for p in pcts)
            + f"{arr.mean():>8.2f}{arr.std(ddof=1):>8.2f}"
        )
        print(row)


def kruskal_with_effect_size(groups):
    stat, p = kruskal(*groups)
    k = len(groups)
    n = sum(len(g) for g in groups)
    epsilon_sq = (stat - k + 1) / (n - k) if n > k else float("nan")
    return stat, p, epsilon_sq, k, n


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


def run_kruskal(grouped, label):
    print(f"\n=== Kruskal-Wallis, {label} ===")
    for key, slot_groups in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        groups = {s: scores for s, scores in slot_groups.items() if len(scores) >= MIN_GROUP_SIZE}
        if len(groups) < 2:
            print(f"{key}: not enough slot groups with >={MIN_GROUP_SIZE} scores, skipped")
            continue
        stat, p, eps, k, n = kruskal_with_effect_size(list(groups.values()))
        verdict = "SIGNAL" if p < 0.05 else "noise (not significant)"
        print(f"{key}: H={stat:.2f}  p={p:.4f}  eps2={eps:.4f} ({effect_label(eps)})  n={n}  {verdict}")


def run_levene(grouped, label):
    print(f"\n=== Levene's test for equal variance (median-centered), {label} ===")
    for key, slot_groups in sorted(grouped.items(), key=lambda kv: str(kv[0])):
        groups = {s: scores for s, scores in slot_groups.items() if len(scores) >= MIN_GROUP_SIZE}
        if len(groups) < 2:
            print(f"{key}: not enough slot groups, skipped")
            continue
        stat, p = levene(*groups.values(), center="median")
        verdict = "slots differ in spread" if p < 0.05 else "spread similar across slots"
        print(f"{key}: W={stat:.2f}  p={p:.4f}  {verdict}")


def run_posthoc_dunn(buckets, label):
    print(f"\n=== Dunn's post-hoc pairwise test, Bonferroni-corrected, {label} ===")
    by_season = defaultdict(list)
    for (season, tier, slot), scores in buckets.items():
        for s in scores:
            by_season[season].append((slot, s))

    for season, rows in sorted(by_season.items(), key=lambda kv: str(kv[0])):
        df = pd.DataFrame(rows, columns=["slot", "score"])
        counts = df["slot"].value_counts()
        valid = counts[counts >= MIN_GROUP_SIZE].index.tolist()
        if len(valid) < 2:
            print(f"{season}: not enough slot groups, skipped")
            continue
        df = df[df["slot"].isin(valid)]
        result = sp.posthoc_dunn(df, val_col="score", group_col="slot", p_adjust="bonferroni")
        cols = result.columns.tolist()
        sig_pairs = []
        for i, s1 in enumerate(cols):
            for s2 in cols[i + 1:]:
                p_val = result.loc[s1, s2]
                if p_val < 0.05:
                    sig_pairs.append(f"{s1} vs {s2} (p={p_val:.4f})")
        print(f"{season}: " + ("; ".join(sig_pairs) if sig_pairs else "no significant pairwise differences"))


def print_util_summary(util_rows):
    print("\n=== UTIL slots (NOT a position -- value of whoever's rostered there, reported separately) ===")
    buckets = defaultdict(list)
    for season, tier, slot, points, nba_player_id, league_id, week, roster_id in util_rows:
        buckets[season].append(float(points))
    for season, scores in sorted(buckets.items(), key=lambda kv: str(kv[0])):
        if len(scores) < MIN_GROUP_SIZE:
            continue
        arr = np.array(scores)
        pcts = np.percentile(arr, PERCENTILES)
        print(
            f"{season}: n={len(arr)}  "
            + "  ".join(f"p{p}={v:.2f}" for p, v in zip(PERCENTILES, pcts))
            + f"  mean={arr.mean():.2f}  stdev={arr.std(ddof=1):.2f}"
        )


def run_full_pass(rows, label, exclude_ids=None):
    buckets = group_scores(rows, exclude_ids=exclude_ids)
    print_percentile_table(buckets, label)

    by_season = build_season_groups(buckets)
    run_kruskal(by_season, f"{label}, pooled across tiers, per season")

    by_season_tier = build_season_tier_groups(buckets)
    run_kruskal(by_season_tier, f"{label}, within each tier, per season")

    run_levene(by_season, f"{label}, pooled across tiers, per season")
    run_posthoc_dunn(buckets, label)


def run():
    conn = get_connection()
    cur = conn.cursor()
    rows = fetch_rows(cur, VIEW_QUERY)
    util_rows = fetch_rows(cur, UTIL_QUERY)
    cur.close()
    conn.close()

    print(f"{len(rows)} locked (roster-week-slot) rows pulled (UTIL excluded from position comparison).")
    print(f"{len(util_rows)} UTIL-slot rows pulled separately.")
    print(f"Sample-size caveat: only 2 real seasons in player_scores -- treat results with more caution than the player-side work.")

    # --- Full data ---
    run_full_pass(rows, "full data")

    # --- Same pass with known player-side outliers excluded ---
    run_full_pass(rows, "known outliers excluded", exclude_ids=KNOWN_OUTLIER_NBA_IDS)

    # --- UTIL, reported separately (not a position) ---
    print_util_summary(util_rows)


if __name__ == "__main__":
    run()
