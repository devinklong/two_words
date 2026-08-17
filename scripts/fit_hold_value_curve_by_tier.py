"""
Fits hold_wins_pct(k) = a*(1-(1-b)^k) per player tier, on the CONDITIONAL
population the curve is actually used for (games below the player's own
lock_bar, games_remaining_in_week 1-4) -- fitting on the full unconditioned
population was found to under-predict real hold value by 10-16pp. Computes
lock_bar/tier directly from player_season_fantasy_stats (not from
game_lock_signal) to avoid a circular dependency, since this fit feeds INTO
percentage_to_lock.sql, which feeds game_lock_signal.

CENTRALIZED 8/15/26 (docs/patch_list.md #1): calls the shared lock_bar()
SQL function instead of hand-writing GREATEST(floor, avg + mult*stddev).
ABSOLUTE_FLOOR/CEILING_MULTIPLIER below are unchanged and still the
values passed in -- this file always uses the canonical validated
values, so this is one of the "just pass params through" cases, not a
grid-search case. DEPLOY ORDER: lock_bar_function.sql must exist before
running this.

Run: python scripts/fit_hold_value_curve_by_tier.py
"""

import numpy as np
from scipy.optimize import curve_fit

from db_connection import get_connection

# MUST MATCH schema/lock_model/game_lock_signal.sql's lock_bar formula
# (now indirectly, via lock_bar()'s own defaults -- these two constants
# just need to match what lock_bar_function.sql defaults to)
ABSOLUTE_FLOOR = 35
CEILING_MULTIPLIER = 0.5

HOLD_VALUE_BY_TIER_QUERY = """
WITH ranked_pool AS (
    SELECT
        player_id, season_id, avg_fantasy_score, stddev_fantasy_score,
        ROW_NUMBER() OVER (PARTITION BY season_id ORDER BY avg_fantasy_score DESC, player_id) AS rank_in_season
    FROM player_season_fantasy_stats
    WHERE games_played >= 20
      AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35
),
tiered_pool AS (
    SELECT
        *,
        CASE
            WHEN rank_in_season <= 25 THEN '1_elite'
            WHEN rank_in_season <= 75 THEN '2_mid'
            ELSE '3_lower'
        END AS tier,
        lock_bar(avg_fantasy_score, stddev_fantasy_score, %(floor)s, %(mult)s) AS lock_bar
    FROM ranked_pool
),
full_week_games AS (
    -- no WHERE filter here: the window below needs every game in the
    -- player's week, including ones that cleared their lock_bar, or
    -- "best_remaining_score" can never see their actual spike games
    SELECT
        gfswe.player_id, gfswe.season_id, gfswe.week_number, gfswe.game_date,
        gfswe.games_remaining_in_week, gfswe.fantasy_score, tp.tier, tp.lock_bar
    FROM game_fantasy_scores_weekly_effective gfswe
    JOIN tiered_pool tp ON tp.player_id = gfswe.player_id AND tp.season_id = gfswe.season_id
),
with_future AS (
    SELECT
        *,
        MAX(fantasy_score) OVER (
            PARTITION BY player_id, season_id, week_number
            ORDER BY game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM full_week_games
)
SELECT
    tier,
    games_remaining_in_week,
    COUNT(*) AS decision_points,
    100.0 * SUM((best_remaining_score > fantasy_score)::INT) / COUNT(*) AS hold_wins_pct
FROM with_future
WHERE fantasy_score < lock_bar  -- filter to real decision points ONLY after the window ran
  AND games_remaining_in_week BETWEEN 1 AND 4
GROUP BY tier, games_remaining_in_week
ORDER BY tier, games_remaining_in_week;
"""


def saturating_curve(k, a, b):
    return a * (1 - (1 - b) ** k)


def fetch_data(conn):
    cur = conn.cursor()
    cur.execute(HOLD_VALUE_BY_TIER_QUERY, {"floor": ABSOLUTE_FLOOR, "mult": CEILING_MULTIPLIER})
    rows = cur.fetchall()
    cur.close()
    by_tier = {}
    for tier, k, n, pct in rows:
        by_tier.setdefault(tier, []).append((k, n, pct))
    return by_tier


def upsert_params(conn, tier, a, b):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO hold_value_curve_params_by_tier (tier, a, b, fitted_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (tier)
        DO UPDATE SET a = EXCLUDED.a, b = EXCLUDED.b, fitted_at = EXCLUDED.fitted_at
        """,
        (tier, float(a), float(b)),
    )
    conn.commit()
    cur.close()


def main():
    conn = get_connection()
    by_tier = fetch_data(conn)

    if not by_tier:
        print("No data found -- check player_tiers / game_fantasy_scores_weekly_effective are populated.")
        conn.close()
        return

    for tier in sorted(by_tier.keys()):
        rows = by_tier[tier]
        if len(rows) < 3:
            print(f"Tier {tier}: only {len(rows)} games_remaining levels -- need at least 3 to fit, skipping.")
            continue

        k = np.array([r[0] for r in rows], dtype=float)
        n = np.array([r[1] for r in rows], dtype=int)
        y = np.array([r[2] for r in rows], dtype=float) / 100.0

        # Weight by sample size (binomial SE = sqrt(p(1-p)/n)) so scipy
        # trusts high-n points more; sigma floored to avoid divide-by-near-zero
        sigma = np.sqrt(np.maximum(y * (1 - y), 0.01) / n)

        popt, _ = curve_fit(
            saturating_curve, k, y,
            p0=[0.9, 0.5],
            bounds=([0.5, 0.01], [1.0, 0.99]),
            sigma=sigma,
            absolute_sigma=False,
        )
        a, b = popt
        pred = saturating_curve(k, a, b)
        resid = y - pred
        # Weighted R^2 (matches the weighted fit) so one near-noise
        # small-sample point (e.g. n=6) can't dominate the metric
        weights = 1 / sigma ** 2
        weighted_mean = np.sum(weights * y) / np.sum(weights)
        ss_res = np.sum(weights * resid ** 2)
        ss_tot = np.sum(weights * (y - weighted_mean) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")

        print(f"\n=== Tier: {tier} ===")
        print(f"Fitted curve: hold_wins_pct(k) = {a:.4f} * (1 - (1 - {b:.4f})^k)")
        print(f"R^2 = {r2:.4f}")
        print(f"{'games_remaining':>15} {'n':>8} {'actual':>10} {'predicted':>10} {'residual':>10}")
        for ki, ni, yi, pi in zip(k, n, y, pred):
            flag = "  <-- small sample" if ni < 100 else ""
            print(f"{int(ki):>15} {ni:>8} {yi*100:>9.1f}% {pi*100:>9.1f}% {(yi-pi)*100:>+9.2f}pp{flag}")

        upsert_params(conn, tier, a, b)
        print(f"Saved to hold_value_curve_params_by_tier (tier {tier}).")

    conn.close()
    print("\nDone. Rerun schema/lock_model/percentage_to_lock.sql's verification queries "
          "to confirm the corrected curves are live.")


if __name__ == "__main__":
    main()
