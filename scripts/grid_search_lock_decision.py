"""
Phase 2 grid search v2 (8/9/26) -- REPLACES grid_search_lock_threshold.py.

Fixes the population-drift problem the earlier version had: threshold was
used for BOTH pool membership and the lock decision at once, so raising
it silently swapped in a more elite population every step, making "edge
over naive" partly just reflect "better players are better," not real
decision-quality improvement.

This version holds the POOL FIXED (K_POOL=1.25, T_POOL=35, unchanged from
ownable_player_pool.sql) across the entire search, and instead searches
the two constants in the NEW per-player lock decision
(schema/lock_model/game_lock_signal.sql, Option B):
  - ABSOLUTE_FLOOR: the flat minimum any player must clear
  - CEILING_MULTIPLIER: how far above their OWN mean+stddev they need to
    reach (0.0 = old flat-threshold behavior, included as a sanity anchor)

Since the population never changes across the grid, pool size stays
constant automatically -- no pool-size guardrail needed this time (that
was only necessary because pool size itself was varying before).

Run from the project root:
    python scripts/grid_search_lock_decision.py
"""

import itertools

import pandas as pd

from db_connection import get_connection

K_POOL = 1.25
T_POOL = 35

ABSOLUTE_FLOOR_VALUES = [30, 32, 35, 38, 40]
CEILING_MULTIPLIER_VALUES = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]

TRAIN_SEASONS = ('22021', '22022', '22023')
REPLACEMENT_LEVEL = 30  # see methodology_notes.md's replacement-level assumption note

SIMULATION_QUERY = """
WITH pool AS (
    SELECT player_id, season_id, avg_fantasy_score, stddev_fantasy_score
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + %(k_pool)s * stddev_fantasy_score >= %(t_pool)s
),
pool_games AS (
    SELECT
        gfswe.*,
        GREATEST(
            %(floor)s,
            p.avg_fantasy_score + %(mult)s * p.stddev_fantasy_score
        ) AS lock_bar
    FROM game_fantasy_scores_weekly_effective gfswe
    JOIN pool p ON p.player_id = gfswe.player_id AND p.season_id = gfswe.season_id
    WHERE gfswe.season_id IN %(train_seasons)s
),
first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, fantasy_score AS locked_score
    FROM pool_games
    WHERE fantasy_score >= lock_bar
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
    SELECT player_id, season_id, week_number,
        GREATEST(MAX(fantasy_score), %(replacement)s) AS oracle_score
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

    for floor, mult in itertools.product(ABSOLUTE_FLOOR_VALUES, CEILING_MULTIPLIER_VALUES):
        df = pd.read_sql(
            SIMULATION_QUERY,
            conn,
            params={
                "k_pool": K_POOL, "t_pool": T_POOL,
                "floor": floor, "mult": mult,
                "train_seasons": TRAIN_SEASONS, "replacement": REPLACEMENT_LEVEL,
            },
        )
        row = df.iloc[0].to_dict()
        row["floor"] = floor
        row["mult"] = mult
        results.append(row)
        print(f"floor={floor:>3} mult={mult:.2f} | pool={int(row['pool_player_seasons']):>4} "
              f"| policy={row['avg_policy_banked']:>6} naive={row['avg_naive_banked']:>6} "
              f"oracle={row['avg_oracle']:>6} | edge={row['policy_edge_over_naive']:>+6}")

    conn.close()

    results_df = pd.DataFrame(results)

    # Sanity check: pool size should be IDENTICAL across every row now,
    # since the pool is fixed and only the lock decision varies
    pool_sizes = results_df["pool_player_seasons"].unique()
    if len(pool_sizes) == 1:
        print(f"\nSanity check passed: pool size constant at {int(pool_sizes[0])} "
              f"player-seasons across every combo (as expected -- pool is fixed).")
    else:
        print(f"\nWARNING: pool size varied across combos ({pool_sizes}) -- "
              f"this should not happen with a fixed pool, investigate before trusting results.")

    results_df = results_df.sort_values("policy_edge_over_naive", ascending=False)

    print("\n=== Top 5 (floor, ceiling_multiplier) combos by edge over naive, TRAIN split (2021-24) ===")
    print(results_df.head(5)[["floor", "mult", "pool_player_seasons", "avg_policy_banked",
                               "avg_naive_banked", "avg_oracle", "policy_edge_over_naive"]].to_string(index=False))

    best = results_df.iloc[0]
    print(f"\nBest: floor={best['floor']}, ceiling_multiplier={best['mult']} "
          f"(edge over naive: {best['policy_edge_over_naive']:+.3f})")

    zero_mult = results_df[results_df["mult"] == 0.0].sort_values("policy_edge_over_naive", ascending=False)
    if not zero_mult.empty:
        best_flat = zero_mult.iloc[0]
        print(f"\nFor comparison, best FLAT-threshold-only result (mult=0.0, old-style behavior): "
              f"floor={best_flat['floor']}, edge={best_flat['policy_edge_over_naive']:+.3f}")
        print("If the overall best above clearly beats this, the self-relative ceiling requirement "
              "is adding real value over a flat bar alone.")

    print("\nNEXT STEP (manual): rerun with the chosen (floor, mult) against the VALIDATE "
          "split (2024-26) via scripts/validate_lock_decision.py.")

    return results_df


if __name__ == "__main__":
    run_grid_search()
