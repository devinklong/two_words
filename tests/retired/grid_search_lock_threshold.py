"""
Phase 2 grid search: loops the weekly-outcome simulation
(schema/analysis/weekly_outcome_simulation.sql's logic) across candidate
(k, threshold) pairs, TRAIN SPLIT ONLY (2021-24), and reports which combo
actually banks the most points relative to the naive baseline -- not just
which combo has the best clear rate (that was Phase 1's limitation).

Both k (pool eligibility multiplier) and threshold (the flat LOCK bar)
are searched together, since pool membership and the lock decision are
both affected by threshold: a higher threshold changes both who's in the
pool (mean + k*stddev >= threshold) AND when a game clears the bar.

Deliberately extends the threshold range above Phase 1's original grid
(35-48) up to 50, given the 8/9/26 finding that 35 sits at the very
bottom of the ~35.6-48.9 points-needed-per-slot range implied by real
weekly team totals (320-440 / 9 starters).

VALIDATION IS SEPARATE AND MANUAL: this script only searches on training
data. Once it reports a winning (k, threshold), rerun the same query by
hand with the season_id filter changed to ('22024','22025') to check the
winner holds up out-of-sample -- do NOT let this script auto-validate,
that would defeat the point of a held-out split.

CENTRALIZED 8/22/26 (docs/architecture_risks.md #8): TRAIN_SEASONS and
REPLACEMENT_LEVEL now imported from scripts/constants.py instead of
redefined here -- no behavior change, same literal values as before.

Run from the project root:
    python scripts/grid_search_lock_threshold.py
"""

import itertools

import pandas as pd

from db_connection import get_connection
from constants import TRAIN_SEASONS, REPLACEMENT_LEVEL

K_VALUES = [0.75, 1.0, 1.25]
THRESHOLD_VALUES = [30, 32, 35, 38, 40, 41, 42, 43, 44, 45, 48, 50]

# Pool-size guardrail (8/9/26): edge over naive climbs monotonically with
# threshold with no cap, since a tiny pool of only elite players trivially
# beats a naive rule -- that's a degenerate answer, not a useful one. The
# project's own target pool size is ~150-210 (30 taxi-squad presumed non-
# lockable, ~160 remaining minus ~10 for in-season dilution -- see
# methodology_notes.md). Only combos within this range are eligible to be
# picked as "best" -- everything outside it is printed for visibility but
# excluded from the winner selection, so the search can't wander into
# "just lock the top 20 superstars" territory and call that a win.
POOL_SIZE_MIN = 140
POOL_SIZE_MAX = 220

SIMULATION_QUERY = """
WITH pool AS (
    SELECT player_id, season_id
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + %(k)s * stddev_fantasy_score >= %(t)s
),
pool_games AS (
    SELECT gfswe.*
    FROM game_fantasy_scores_weekly_effective gfswe
    JOIN pool p ON p.player_id = gfswe.player_id AND p.season_id = gfswe.season_id
    WHERE gfswe.season_id IN %(train_seasons)s
),
first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS locked_score
    FROM pool_games
    WHERE fantasy_score >= %(t)s
    ORDER BY player_id, season_id, week_number, game_date ASC
),
last_game AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS final_score
    FROM pool_games
    WHERE games_remaining_in_week = 0
    ORDER BY player_id, season_id, week_number, game_date DESC
),
oracle AS (
    SELECT player_id, season_id, week_number, MAX(fantasy_score) AS oracle_score
    FROM pool_games
    GROUP BY player_id, season_id, week_number
),
naive_first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS naive_locked_score
    FROM pool_games
    WHERE fantasy_score >= 30.3
    ORDER BY player_id, season_id, week_number, game_date ASC
),
player_weeks AS (
    SELECT DISTINCT player_id, season_id, week_number FROM pool_games
),
banked AS (
    SELECT
        pw.player_id, pw.season_id, pw.week_number,
        COALESCE(fl.locked_score, GREATEST(lg.final_score, %(replacement)s)) AS policy_banked_score,
        COALESCE(nfl.naive_locked_score, GREATEST(lg.final_score, %(replacement)s)) AS naive_banked_score,
        o.oracle_score
    FROM player_weeks pw
    JOIN oracle o USING (player_id, season_id, week_number)
    JOIN last_game lg USING (player_id, season_id, week_number)
    LEFT JOIN first_lock fl USING (player_id, season_id, week_number)
    LEFT JOIN naive_first_lock nfl USING (player_id, season_id, week_number)
)
SELECT
    COUNT(DISTINCT player_id || '-' || season_id) AS pool_player_seasons,
    COUNT(*) AS player_weeks_simulated,
    ROUND(AVG(policy_banked_score)::NUMERIC, 3) AS avg_policy_banked,
    ROUND(AVG(naive_banked_score)::NUMERIC, 3) AS avg_naive_banked,
    ROUND(AVG(oracle_score)::NUMERIC, 3) AS avg_oracle,
    ROUND((AVG(policy_banked_score) - AVG(naive_banked_score))::NUMERIC, 3) AS policy_edge_over_naive
FROM banked;
"""


def run_grid_search():
    conn = get_connection()
    results = []

    for k, t in itertools.product(K_VALUES, THRESHOLD_VALUES):
        df = pd.read_sql(
            SIMULATION_QUERY,
            conn,
            params={"k": k, "t": t, "train_seasons": TRAIN_SEASONS, "replacement": REPLACEMENT_LEVEL},
        )
        row = df.iloc[0].to_dict()
        row["k"] = k
        row["threshold"] = t
        results.append(row)
        print(f"k={k:.2f} t={t:>3} | pool={int(row['pool_player_seasons']):>4} "
              f"| policy={row['avg_policy_banked']:>6} naive={row['avg_naive_banked']:>6} "
              f"oracle={row['avg_oracle']:>6} | edge={row['policy_edge_over_naive']:>+6}")

    conn.close()

    results_df = pd.DataFrame(results)
    results_df["in_target_pool_range"] = results_df["pool_player_seasons"].between(
        POOL_SIZE_MIN, POOL_SIZE_MAX
    )

    in_range = results_df[results_df["in_target_pool_range"]].sort_values(
        "policy_edge_over_naive", ascending=False
    )
    out_of_range_best = results_df[~results_df["in_target_pool_range"]].sort_values(
        "policy_edge_over_naive", ascending=False
    ).head(3)

    print(f"\n=== Top 5 (k, threshold) combos, RESTRICTED to pool size {POOL_SIZE_MIN}-{POOL_SIZE_MAX} "
          f"(the actual target population) ===")
    if in_range.empty:
        print(f"No combos landed in the {POOL_SIZE_MIN}-{POOL_SIZE_MAX} range -- widen "
              f"THRESHOLD_VALUES or the guardrail range and rerun.")
    else:
        print(in_range.head(5)[["k", "threshold", "pool_player_seasons",
                                 "avg_policy_banked", "avg_naive_banked",
                                 "avg_oracle", "policy_edge_over_naive"]].to_string(index=False))

        best = in_range.iloc[0]
        print(f"\nBest (within target pool size): k={best['k']}, threshold={best['threshold']} "
              f"(edge over naive: {best['policy_edge_over_naive']:+.3f}, "
              f"pool size: {int(best['pool_player_seasons'])} player-seasons)")

    print(f"\n=== For reference: top out-of-range combos (excluded as degenerate -- "
          f"pool too small/large to represent the tool's actual target population) ===")
    print(out_of_range_best[["k", "threshold", "pool_player_seasons",
                              "policy_edge_over_naive"]].to_string(index=False))

    print("\nNEXT STEP (manual, not automated by this script): rerun the same simulation "
          "with the chosen (k, threshold) against the VALIDATE split (2024-26) via "
          "scripts/validate_lock_threshold.py to confirm the edge holds up out-of-sample "
          "before treating this as the new calibrated config.")

    return results_df


if __name__ == "__main__":
    run_grid_search()
