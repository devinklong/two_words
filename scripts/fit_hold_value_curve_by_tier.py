"""
REPLACES the old fit_hold_value_curve.py (variance-bucket based).

Fits hold_wins_pct(k) = a*(1-(1-b)^k) PER TIER (elite/mid/lower, see
player_tiers.sql), fit ONLY on the population the curve will actually be
used for: games where fantasy_score < lock_bar (the player's own
self-relative ceiling, GREATEST(35, avg + 0.5*stddev) -- MUST match
game_lock_signal.sql's formula exactly) AND games_remaining_in_week
BETWEEN 1 and 4.

WHY THIS REPLACES THE OLD FIT (8/9/26): the old curve was fit on the
FULL unconditioned population -- every game, regardless of whether it
already cleared the player's own bar. Checked empirically
(hold_value_by_tier_and_grw.sql) and found the old curve systematically
UNDER-predicted actual hold value by 10-16 percentage points once
restricted to only the games where the decision is actually live (below
the player's own ceiling). That's a real selection-bias problem, not
noise -- a below-a-player's-own-norm game is naturally much easier for a
later game to beat than a typical game is, and the old curve had no way
to know it was being applied to that specific subset. Refitting directly
on the conditional population it's meant for fixes this properly instead
of patching around the mismatch.

Deliberately computes lock_bar and tier directly from
player_season_fantasy_stats here, NOT from game_lock_signal -- avoids a
circular dependency (this fit feeds INTO percentage_to_lock.sql, which
feeds into game_lock_signal; it shouldn't depend on game_lock_signal's
own output). The lock_bar formula below must be kept in sync with
game_lock_signal.sql's CASE logic by hand if that ever changes.

Run from the project root:
    python scripts/fit_hold_value_curve_by_tier.py
"""

import numpy as np
from scipy.optimize import curve_fit

from db_connection import get_connection

# MUST MATCH schema/lock_model/game_lock_signal.sql's lock_bar formula
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
        GREATEST(%(floor)s, avg_fantasy_score + %(mult)s * stddev_fantasy_score) AS lock_bar
    FROM ranked_pool
),
full_week_games AS (
    -- NO WHERE filter here -- the window function below needs every game
    -- in the player's week, including ones that cleared their lock_bar,
    -- or "best_remaining_score" can never see a player's actual spike
    -- games as candidates. Same bug class as the original
    -- hold_value_step_function.sql fix earlier this project.
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
WHERE fantasy_score < lock_bar  -- filter to actual decision points ONLY after the window ran
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

        # Weight by sample size: a proportion from n=1665 should count far
        # more than one from n=6. Standard error of a binomial proportion
        # is sqrt(p*(1-p)/n) -- used as sigma so scipy trusts high-n points
        # more. Floor sigma to avoid a divide-by-near-zero blowup on a
        # point that happens to land exactly at 0% or 100%.
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
        # Weighted R^2, consistent with the weighted fit above -- an
        # unweighted R^2 over only 4 points lets a single near-noise
        # small-sample point (e.g. n=6) dominate the metric even when the
        # fit is excellent for the points covering 99%+ of real decisions.
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
