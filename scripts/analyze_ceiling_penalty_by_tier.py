"""
Tests the hypothesis (8/9/26): does the ceiling_multiplier penalty hit
elite players harder than lower-tier pool players? Compares oracle
capture (avg_policy_banked / avg_oracle) at mult=0 vs the validated
mult=0.5, split by rank tier (by avg_fantasy_score, within season),
fixed floor=35, fixed pool (k=1.25, threshold=35), TRAIN split only.

If elite players' pct_of_oracle drops MORE than lower tiers' when adding
the ceiling requirement, that's real evidence a single global multiplier
over-penalizes stars specifically -- worth then testing whether the
multiplier should vary by tier (mirroring how percentage_to_lock was
bucketed by variance instead of using one pooled curve).

CENTRALIZED 8/15/26 (docs/patch_list.md #1): calls the shared lock_bar()
SQL function, passing mult as an explicit param since THIS script's
whole purpose is comparing two different mult values (0.0 vs 0.5) --
lock_bar()'s default (0.5) is only used for the mult=0.5 run, not
hardcoded separately here. DEPLOY ORDER: lock_bar_function.sql must
exist before running this.

Run from the project root:
    python scripts/analyze_ceiling_penalty_by_tier.py
"""

import pandas as pd

from db_connection import get_connection

K_POOL = 1.25
T_POOL = 35
FLOOR = 35
TRAIN_SEASONS = ('22021', '22022', '22023')
REPLACEMENT_LEVEL = 30

TIER_QUERY = """
WITH pool AS (
    SELECT player_id, season_id, avg_fantasy_score, stddev_fantasy_score
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + %(k_pool)s * stddev_fantasy_score >= %(t_pool)s
),
ranked_pool AS (
    SELECT
        p.*,
        ROW_NUMBER() OVER (PARTITION BY p.season_id ORDER BY p.avg_fantasy_score DESC, p.player_id) AS rank_in_season
    FROM pool p
),
tiered_pool AS (
    SELECT
        *,
        CASE
            WHEN rank_in_season <= 25 THEN '1_elite (top 25)'
            WHEN rank_in_season <= 75 THEN '2_mid (26-75)'
            ELSE '3_lower (76+)'
        END AS tier
    FROM ranked_pool
),
pool_games AS (
    SELECT
        gfswe.*,
        tp.tier,
        lock_bar(tp.avg_fantasy_score, tp.stddev_fantasy_score, %(floor)s, %(mult)s) AS lock_bar
    FROM game_fantasy_scores_weekly_effective gfswe
    JOIN tiered_pool tp ON tp.player_id = gfswe.player_id AND tp.season_id = gfswe.season_id
    WHERE gfswe.season_id IN %(train_seasons)s
),
first_lock AS (
    SELECT DISTINCT ON (player_id, season_id, week_number)
        player_id, season_id, week_number, tier, fantasy_score AS locked_score
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
    SELECT player_id, season_id, week_number, tier,
        GREATEST(MAX(fantasy_score), %(replacement)s) AS oracle_score
    FROM pool_games
    GROUP BY player_id, season_id, week_number, tier
),
player_weeks AS (
    SELECT DISTINCT player_id, season_id, week_number, tier FROM pool_games
),
banked AS (
    SELECT
        pw.tier,
        COALESCE(fl.locked_score, GREATEST(lg.final_score, %(replacement)s)) AS policy_banked_score,
        o.oracle_score
    FROM player_weeks pw
    JOIN oracle o USING (player_id, season_id, week_number, tier)
    JOIN last_game lg USING (player_id, season_id, week_number)
    LEFT JOIN first_lock fl USING (player_id, season_id, week_number, tier)
)
SELECT
    tier,
    COUNT(*) AS player_weeks,
    ROUND(AVG(policy_banked_score)::NUMERIC, 3) AS avg_policy_banked,
    ROUND(AVG(oracle_score)::NUMERIC, 3) AS avg_oracle,
    ROUND((100.0 * AVG(policy_banked_score) / AVG(oracle_score))::NUMERIC, 2) AS pct_of_oracle
FROM banked
GROUP BY tier
ORDER BY tier;
"""


def main():
    conn = get_connection()

    print("=== mult=0.0 (no ceiling requirement, bar = GREATEST(35, own avg)) ===")
    df0 = pd.read_sql(TIER_QUERY, conn, params={
        "k_pool": K_POOL, "t_pool": T_POOL, "floor": FLOOR, "mult": 0.0,
        "train_seasons": TRAIN_SEASONS, "replacement": REPLACEMENT_LEVEL,
    })
    print(df0.to_string(index=False))

    print("\n=== mult=0.5 (validated config) ===")
    df05 = pd.read_sql(TIER_QUERY, conn, params={
        "k_pool": K_POOL, "t_pool": T_POOL, "floor": FLOOR, "mult": 0.5,
        "train_seasons": TRAIN_SEASONS, "replacement": REPLACEMENT_LEVEL,
    })
    print(df05.to_string(index=False))

    conn.close()

    merged = df0.merge(df05, on="tier", suffixes=("_mult0", "_mult05"))
    merged["oracle_pct_drop"] = merged["pct_of_oracle_mult0"] - merged["pct_of_oracle_mult05"]

    print("\n=== Cost of adding the ceiling requirement, by tier ===")
    print(merged[["tier", "pct_of_oracle_mult0", "pct_of_oracle_mult05", "oracle_pct_drop"]].to_string(index=False))
    print("\nIf '1_elite' shows a bigger oracle_pct_drop than the other tiers, that's real evidence")
    print("a single global ceiling_multiplier over-penalizes top players specifically.")


if __name__ == "__main__":
    main()
