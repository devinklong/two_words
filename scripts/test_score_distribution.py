"""Test the normal-scores assumption behind the lock calculator against five seasons of real games. Read-only.

The model treats a player's game scores as draws from a normal distribution around his season mean (that is what the hold curve and the
bar are built on). Spike players are probably right-skewed, so this checks it two ways, on the same player-seasons the model uses
(20+ games and mean + 1.25 sd >= 35):

  1 Shape    z = (score - season mean) / season sd for every game: skew, excess kurtosis, and how often real games land above +0.5, +1, +1.5 and
             +2 sd compared with a normal curve (30.9%, 15.9%, 6.7%, 2.3%). Overall and by tier (1_elite = top 25 by mean in a season, 2_mid to 75).
  2 Outcome  for every game at or above the lock bar with 1-3 games left in the week: how often a later game that week really beat the score
             (empirical), against what the normal model predicts, 1 - Phi(z)^k for k later games (every later game assumed played). A big gap
             means the normal model mis-prices a lock. The model's own LOCK-right odds (nba_lock_win_rate) are the empirical column; this shows
             which way the normal model errs and by how much.

Season mean and sd are the same season's (in sample), like the win-rate view, so the shape numbers are optimistic about the model; the
direction of any gap is still informative. Seasons loaded: every season in player_season_fantasy_stats.

Usage (from ~/projects/two_words):  python scripts/test_score_distribution.py [--min-n 100]
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from scipy import stats

sys.path.append(str(Path(__file__).resolve().parent))

POOL_SQL = """
WITH ranked AS (
    SELECT player_id, season_id, avg_fantasy_score AS mu, stddev_fantasy_score AS sd,
           ROW_NUMBER() OVER (PARTITION BY season_id ORDER BY avg_fantasy_score DESC, player_id) AS rk
    FROM player_season_fantasy_stats
    WHERE games_played >= 20 AND stddev_fantasy_score > 0 AND avg_fantasy_score + 1.25 * stddev_fantasy_score >= 35),
pool AS (SELECT *, CASE WHEN rk <= 25 THEN '1_elite' WHEN rk <= 75 THEN '2_mid' ELSE '3_lower' END AS tier,
                 lock_bar(mu, sd, 35, 0.5) AS bar FROM ranked),
g AS (
    SELECT p.tier, p.mu, p.sd, p.bar, e.fantasy_score AS score, e.games_remaining_in_week AS left_in_week,
           MAX(e.fantasy_score) OVER (PARTITION BY e.player_id, e.season_id, e.week_number ORDER BY e.game_date DESC
                                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS best_after
    FROM game_fantasy_scores_weekly_effective e JOIN pool p ON p.player_id = e.player_id AND p.season_id = e.season_id)
SELECT tier, mu, sd, bar, score, left_in_week, best_after FROM g
"""


def bucket(gap):
    return "0-5" if gap < 5 else "5-10" if gap < 10 else "10-15" if gap < 15 else "15+"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-n", type=int, default=100, help="smallest group that gets a verdict")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute(POOL_SQL)
    rows = cur.fetchall()
    cur.close(); conn.close()
    tier = np.array([r[0] for r in rows])
    mu, sd, bar, score = (np.array([float(r[i]) for r in rows]) for i in (1, 2, 3, 4))
    left = np.array([int(r[5]) if r[5] is not None else 0 for r in rows])
    best_after = np.array([float(r[6]) if r[6] is not None else -1.0 for r in rows])
    z = (score - mu) / sd
    print(f"{len(rows)} player-games in the model's pool\n")

    print("1 SHAPE: share of real games above +k sd (normal curve in brackets)")
    print(f"  {'group':<9} {'games':>7} {'skew':>6} {'ex.kurt':>8}  {'>+0.5sd':>14} {'>+1sd':>14} {'>+1.5sd':>14} {'>+2sd':>14}")
    verdict_fat = False
    for name, mask in [("all", np.ones(len(z), bool))] + [(t, tier == t) for t in sorted(set(tier))]:
        zz = z[mask]
        cells = []
        for k in (0.5, 1.0, 1.5, 2.0):
            cells.append(f"{(zz > k).mean() * 100:5.1f}% [{(1 - stats.norm.cdf(k)) * 100:4.1f}%]")
        print(f"  {name:<9} {len(zz):>7} {stats.skew(zz):>6.2f} {stats.kurtosis(zz):>8.2f}  " + " ".join(f"{c:>14}" for c in cells))
        if name == "all" and (zz > 2.0).mean() > 1.3 * (1 - stats.norm.cdf(2.0)):
            verdict_fat = True
    print("  skew > 0 = right tail longer than normal; positive excess kurtosis = fatter tails than normal.")
    print(f"  Verdict: {'upper tail is FATTER than the normal model assumes (more big games than predicted)' if verdict_fat else 'upper tail is close to normal'}\n")

    print("2 OUTCOME: at or above the lock bar, how often a later game beat the score")
    print(f"  {'left':>4} {'above bar':>9} {'n':>7} {'real':>7} {'normal':>7} {'real-normal':>12}")
    over = under = 0
    for k in (1, 2, 3):
        for b in ("0-5", "5-10", "10-15", "15+"):
            m = (score >= bar) & (left == k) & np.array([bucket(s - r) == b for s, r in zip(score, bar)])
            n = int(m.sum())
            if n == 0:
                continue
            real = float((best_after[m] > score[m]).mean())
            pred = float((1 - stats.norm.cdf(z[m]) ** k).mean())
            flag = ""
            if n >= args.min_n and abs(real - pred) > 0.05:
                flag = "  <-- model " + ("OVER" if pred > real else "UNDER") + "-states a later beat"
                over += pred > real
                under += pred < real
            print(f"  {k:>4} {b:>9} {n:>7} {real * 100:>6.1f}% {pred * 100:>6.1f}% {(real - pred) * 100:>+11.1f}{flag}")
    print("  OVER = the normal model says a later game is more likely to beat your lock than really happened (it tells you to HOLD too often);")
    print("  UNDER = it says a later game is less likely than really happened (it tells you to lock too early).")
    print(f"\nGroups with a gap over 5 points and {args.min_n}+ cases: {int(over)} OVER, {int(under)} UNDER")


if __name__ == "__main__":
    main()
