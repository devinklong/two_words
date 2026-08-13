"""
scripts/analysis/team_metrics_vs_fantasy_score_within_player.py

Retests the 3 metrics that cleared the bar (own_off_rating, opp_def_rating,
opp_pace) against each player's OWN deviation from their own baseline mean,
not pooled fantasy_score -- same self-relative philosophy as the project's
lock-bar model. Separates "this player performs better when team context X
is true" (real, useful) from "good teams roster better players" (a fixed
composition effect, not a timing signal). Read-only.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

N_BUCKETS = 5
MIN_GAMES = 10  # fully-populated trailing-10 windows only, apples-to-apples
MIN_PLAYER_GAMES = 20  # a player needs enough games for their own mean to be stable

METRICS = ["own_off_rating", "opp_def_rating", "opp_pace"]

QUERY = """
    SELECT gfsw.player_id, tvo.own_games_included,
           tvo.own_off_rating, tvo.opp_def_rating, tvo.opp_pace,
           gfsw.fantasy_score
    FROM team_vs_opponent_trailing10 tvo
    JOIN game_fantasy_scores_weekly_lock_signal gfsw
        ON gfsw.team_id = tvo.team_id
        AND gfsw.season_id = tvo.season_id
        AND gfsw.game_date = tvo.game_date
    WHERE gfsw.fantasy_score IS NOT NULL;
"""


def test_metric(df, metric):
    sub = df[[metric, "fantasy_score_deviation"]].dropna().copy()
    corr = sub[metric].corr(sub["fantasy_score_deviation"])

    sub["bucket"] = pd.qcut(sub[metric], q=N_BUCKETS, duplicates="drop")
    summary = sub.groupby("bucket", observed=True)["fantasy_score_deviation"].agg(
        ["count", "mean"]
    ).round(3)

    means = summary["mean"].tolist()
    diffs = [round(b - a, 3) for a, b in zip(means, means[1:])]
    monotonic = all(d >= 0 for d in diffs) or all(d <= 0 for d in diffs)
    swing = round(means[-1] - means[0], 3)

    return {"metric": metric, "n": len(sub), "corr": round(corr, 4),
            "monotonic": monotonic, "swing": swing, "summary": summary}


def run():
    conn = get_connection()
    df = pd.read_sql(QUERY, conn)
    conn.close()

    df = df[df["own_games_included"] >= MIN_GAMES].copy()

    player_game_counts = df.groupby("player_id")["fantasy_score"].transform("count")
    df = df[player_game_counts >= MIN_PLAYER_GAMES].copy()
    print(f"n rows (games_included >= {MIN_GAMES}, player has >= {MIN_PLAYER_GAMES} games): {len(df)}")
    print(f"n unique players: {df['player_id'].nunique()}")

    player_mean = df.groupby("player_id")["fantasy_score"].transform("mean")
    df["fantasy_score_deviation"] = df["fantasy_score"] - player_mean

    results = []
    for metric in METRICS:
        print()
        print("=" * 70)
        print(f"METRIC: {metric}  vs  fantasy_score_deviation (within-player)")
        print("=" * 70)
        r = test_metric(df, metric)
        results.append(r)
        print(r["summary"].to_string())
        print(f"\n  Correlation: {r['corr']}   Monotonic: {r['monotonic']}   Swing: {r['swing']}")

    print()
    print("=" * 70)
    print("SUMMARY -- within-player deviation, pooled result alongside for comparison")
    print("=" * 70)
    pooled_swings = {"own_off_rating": 3.293, "opp_def_rating": 2.476, "opp_pace": 1.918}
    for r in sorted(results, key=lambda r: abs(r["swing"]), reverse=True):
        pooled = pooled_swings[r["metric"]]
        shrink_pct = round(100 * (1 - abs(r["swing"]) / abs(pooled)), 1) if pooled != 0 else None
        print(f"  {r['metric']:16s} pooled_swing={pooled:+.3f}  within_player_swing={r['swing']:+.3f}  "
              f"shrink={shrink_pct}%")
    print()
    print("  A swing that survives near its pooled size is a real within-player timing effect --")
    print("  worth a targeted backtest. A swing that collapses toward zero was mostly the")
    print("  composition confound (good teams roster better players), same pattern as the")
    print("  B2B-vs-percentage_to_lock confound found earlier.")


if __name__ == "__main__":
    run()
