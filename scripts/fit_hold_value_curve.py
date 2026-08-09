"""
Fits the saturating curve y = a * (1 - (1-b)^k) SEPARATELY for each player
variance bucket (schema/percentage_to_lock.sql's player_variance_buckets),
where k = games remaining in the week and y = hold_wins_pct within that
bucket. Writes the fitted a/b directly into hold_value_curve_params — no
manual SQL editing required.

BUCKETED, NOT POOLED (as of 8/8/26): a single pooled curve treats a
high-variance streamer and a steady, consistent player identically, even
though the whole point of the lock/hold decision is player-specific
volatility. Splitting by stddev_fantasy_score (already computed in
player_season_fantasy_stats) into two tiers lets percentage_to_lock
reflect that difference, while each bucket still pools thousands of
decision points — enough to fit a stable curve, unlike a true per-player
fit (a single player's season rarely has more than ~15-20 relevant weeks,
nowhere near enough to fit 2 parameters reliably on its own).

WHY THIS SHAPE: confirmed 8/9/26 the pooled relationship is NOT linear —
diminishing marginal returns per additional game remaining, consistent
with correlated (not independent) future performance within a week. See
methodology_notes.md for the full reasoning.

Run from the project root (creates/updates rows in hold_value_curve_params
for every bucket found — safe to rerun any time, e.g. after a season
backfill):
    python scripts/fit_hold_value_curve.py

Prereq: schema/percentage_to_lock.sql has been run at least once, so
player_variance_buckets and hold_value_curve_params exist.
"""

import numpy as np
from scipy.optimize import curve_fit

from db_connection import get_connection

BUCKETED_HOLD_VALUE_QUERY = """
WITH future_scores AS (
    SELECT
        COALESCE(pvb.variance_bucket, 1) AS variance_bucket,
        gfsw.games_remaining_in_week,
        gfsw.fantasy_score,
        MAX(gfsw.fantasy_score) OVER (
            PARTITION BY gfsw.player_id, gfsw.season_id, gfsw.week_number
            ORDER BY gfsw.game_date DESC
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS best_remaining_score
    FROM game_fantasy_scores_weekly_effective gfsw
    LEFT JOIN player_variance_buckets pvb
        ON pvb.player_id = gfsw.player_id AND pvb.season_id = gfsw.season_id
)
SELECT
    variance_bucket,
    games_remaining_in_week,
    COUNT(*) AS decision_points,
    100.0 * SUM((best_remaining_score > fantasy_score)::INT) / COUNT(*) AS hold_wins_pct
FROM future_scores
WHERE games_remaining_in_week >= 1
GROUP BY variance_bucket, games_remaining_in_week
ORDER BY variance_bucket, games_remaining_in_week;
"""


def saturating_curve(k, a, b):
    return a * (1 - (1 - b) ** k)


def fetch_bucketed_data(conn):
    cur = conn.cursor()
    cur.execute(BUCKETED_HOLD_VALUE_QUERY)
    rows = cur.fetchall()
    cur.close()
    # group by bucket
    by_bucket = {}
    for bucket, k, n, pct in rows:
        by_bucket.setdefault(bucket, []).append((k, n, pct))
    return by_bucket


def fit_bucket(rows):
    k = np.array([r[0] for r in rows], dtype=float)
    n = np.array([r[1] for r in rows], dtype=int)
    y = np.array([r[2] for r in rows], dtype=float) / 100.0

    popt, _ = curve_fit(
        saturating_curve, k, y,
        p0=[0.9, 0.5],
        bounds=([0.5, 0.01], [1.0, 0.99]),
    )
    a, b = popt
    pred = saturating_curve(k, a, b)
    resid = y - pred
    ss_res = np.sum(resid ** 2)
    ss_tot = np.sum((y - y.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return a, b, r2, k, n, y, pred


def upsert_params(conn, bucket, a, b):
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO hold_value_curve_params (variance_bucket, a, b, fitted_at)
        VALUES (%s, %s, %s, now())
        ON CONFLICT (variance_bucket)
        DO UPDATE SET a = EXCLUDED.a, b = EXCLUDED.b, fitted_at = EXCLUDED.fitted_at
        """,
        (bucket, float(a), float(b)),
    )
    conn.commit()
    cur.close()


def main():
    conn = get_connection()
    by_bucket = fetch_bucketed_data(conn)

    if not by_bucket:
        print("No bucketed hold-value data found — check player_variance_buckets and game_fantasy_scores_weekly_effective are populated.")
        conn.close()
        return

    for bucket in sorted(by_bucket.keys()):
        rows = by_bucket[bucket]
        if len(rows) < 3:
            print(f"Bucket {bucket}: only {len(rows)} games_remaining_in_week levels — need at least 3 to fit, skipping.")
            continue

        a, b, r2, k, n, y, pred = fit_bucket(rows)

        label = "low-variance (steadier)" if bucket == 1 else "high-variance (streakier)"
        print(f"\n=== Bucket {bucket} — {label} ===")
        print(f"Fitted curve: hold_wins_pct(k) = {a:.4f} * (1 - (1 - {b:.4f})^k)")
        print(f"R^2 = {r2:.4f}")
        print(f"{'games_remaining':>15} {'n':>8} {'actual':>10} {'predicted':>10} {'residual':>10}")
        for ki, ni, yi, pi in zip(k, n, y, pred):
            flag = "  <-- small sample, low confidence" if ni < 1000 else ""
            print(f"{int(ki):>15} {ni:>8} {yi*100:>9.1f}% {pi*100:>9.1f}% {(yi-pi)*100:>+9.2f}pp{flag}")

        upsert_params(conn, bucket, a, b)
        print(f"Saved to hold_value_curve_params (bucket {bucket}).")

    conn.close()
    print("\nDone. game_fantasy_scores_weekly_lock_signal will now use these bucketed curves.")


if __name__ == "__main__":
    main()
