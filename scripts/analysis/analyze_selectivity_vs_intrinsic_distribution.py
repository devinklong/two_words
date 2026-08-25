"""
scripts/analysis/analyze_selectivity_vs_intrinsic_distribution.py

Distinguishes two competing explanations for why the Center SLOT
outscores other slots in real locked decisions
(locked_scores_by_slot), even though raw per-game Center distributions
are NOT uniformly higher (player_scores_by_position_tier):

  H1 SELECTIVE LOCKING: owners are more patient/selective locking their
     one C slot specifically (no generic C-flex the way G/F have),
     waiting for a better game before committing.
  H2 INTRINSIC RIGHT TAIL: Centers just have a fatter right tail in
     their raw distribution -- the same selectivity applied equally
     everywhere would still produce a higher C average, no special
     C-specific patience required. This is also the more directly
     relevant question for lock_bar itself, which is explicitly
     designed to chase each player's own right tail
     (GREATEST(35, mean + 0.5*stddev)).

Only 2024 and 2025 are used (the only seasons with real locked data in
player_scores) -- raw distributions are pulled from the SAME two
seasons for a fair comparison, not the full 5-season player-side
history. Only the 5 dedicated positions (PG/SG/SF/PF/C) are included --
G, F, and UTIL slots don't map to one single raw position, so a direct
percentile-within-position comparison isn't meaningful for them.

DELIBERATELY Python, not multi-CTE SQL: matches the established pattern
of every other script in this suite (analyze_position_scoring_
distributions.py, analyze_slot_scoring_distributions.py) -- one cheap,
single-pass SELECT per data source, all percentile/rank math done here
with numpy. An earlier SQL-only version of this check used per-row
correlated subqueries against player_scores_by_position_tier (a view
built on a ~146k-row join), which forced that join to be recomputed on
every row and caused a real production incident (runaway query, stale
Postgres shared-memory lock, service down) -- this version pulls each
source exactly once and can't repeat that mistake structurally.
"""

import sys
from pathlib import Path
from collections import defaultdict

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np

DEDICATED_POSITIONS = {"PG", "SG", "SF", "PF", "C"}

RAW_SCORES_QUERY = """
    SELECT season_id, tier, position, fantasy_score
    FROM player_scores_by_position_tier
    WHERE season_id IN ('22024', '22025')
      AND position IN ('PG', 'SG', 'SF', 'PF', 'C');
"""

LOCKED_SCORES_QUERY = """
    SELECT season, tier, slot, points
    FROM locked_scores_by_slot
    WHERE slot IN ('PG', 'SG', 'SF', 'PF', 'C');
"""


def fetch_raw_scores(cur):
    """One clean pull -- buckets by (season_id, tier, position) -> sorted
    numpy array of scores, ready for fast percentile-rank lookups."""
    cur.execute(RAW_SCORES_QUERY)
    buckets = defaultdict(list)
    for season_id, tier, position, fantasy_score in cur.fetchall():
        buckets[(season_id, tier, position)].append(float(fantasy_score))
    return {key: np.sort(np.array(scores)) for key, scores in buckets.items()}


def fetch_locked_scores(cur):
    cur.execute(LOCKED_SCORES_QUERY)
    return cur.fetchall()


def percentile_rank(sorted_raw_array, value):
    """Fraction of raw_array <= value, as a percentile (0-100) --
    np.searchsorted on a pre-sorted array is O(log n), no per-row
    recomputation of anything expensive."""
    n_at_or_below = np.searchsorted(sorted_raw_array, value, side="right")
    return 100.0 * n_at_or_below / len(sorted_raw_array)


def run_selectivity_test(raw_scores, locked_rows):
    """H1 test: average percentile rank of locked scores within their
    OWN position's raw distribution, per (season, position). A markedly
    higher average for C than for other positions is real evidence of
    C-specific selective locking behavior; similar averages across
    positions point to H2 instead (the raw distribution shape already
    explains it, no special behavior needed)."""
    print("\n=== Selectivity test: avg percentile rank of locked scores within their own raw distribution ===")
    ranks_by_season_position = defaultdict(list)
    skipped = 0
    for season, tier, slot, points in locked_rows:
        season_id = f"2{season}"
        key = (season_id, tier, slot)
        sorted_raw = raw_scores.get(key)
        if sorted_raw is None or len(sorted_raw) == 0:
            skipped += 1
            continue
        ranks_by_season_position[(season, slot)].append(percentile_rank(sorted_raw, float(points)))

    if skipped:
        print(f"({skipped} locked row(s) skipped -- no matching raw distribution bucket)")

    print(f"{'season':<8}{'slot':<6}{'n':>6}{'avg_percentile_rank':>22}")
    for (season, slot), ranks in sorted(ranks_by_season_position.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        print(f"{str(season):<8}{slot:<6}{len(ranks):>6}{np.mean(ranks):>22.1f}")


def run_right_tail_test(raw_scores):
    """H2 test, fully independent of any locking behavior: for each
    position's raw distribution, compares upper-tail spread (p90-p50)
    against lower/typical spread (p50-p10). Near 1.0 = symmetric. Well
    above 1.0 = disproportionate right-tail room -- exactly the shape
    lock_bar's ceiling-chasing design (mean + 0.5*stddev) is built to
    exploit, with zero behavioral component."""
    print("\n=== Intrinsic right-tail test: (p90-p50)/(p50-p10) per raw distribution, no locking behavior involved ===")
    print(f"{'season':<8}{'tier':<10}{'pos':<5}{'n':>6}{'p10':>8}{'p50':>8}{'p90':>8}{'right_tail_ratio':>18}")
    for (season_id, tier, position), scores in sorted(
        raw_scores.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]), kv[0][2])
    ):
        if len(scores) < 10:
            continue
        p10, p50, p90 = np.percentile(scores, [10, 50, 90])
        lower_spread = p50 - p10
        upper_spread = p90 - p50
        ratio = upper_spread / lower_spread if lower_spread > 0 else float("nan")
        print(
            f"{str(season_id):<8}{str(tier):<10}{position:<5}{len(scores):>6}"
            f"{p10:>8.2f}{p50:>8.2f}{p90:>8.2f}{ratio:>18.2f}"
        )


def run():
    conn = get_connection()
    cur = conn.cursor()
    raw_scores = fetch_raw_scores(cur)
    locked_rows = fetch_locked_scores(cur)
    cur.close()
    conn.close()

    total_raw = sum(len(v) for v in raw_scores.values())
    print(f"{total_raw} raw score(s) pulled across {len(raw_scores)} (season, tier, position) bucket(s).")
    print(f"{len(locked_rows)} locked score(s) pulled (dedicated positions only, G/F/UTIL excluded).")

    run_selectivity_test(raw_scores, locked_rows)
    run_right_tail_test(raw_scores)


if __name__ == "__main__":
    run()
