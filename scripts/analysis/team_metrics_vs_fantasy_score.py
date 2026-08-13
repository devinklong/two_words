"""
scripts/analysis/team_metrics_vs_fantasy_score.py

Every roadmap-step-5 hypothesis so far was tested against percentage_to_lock
(structurally blind to game context) or own_off_rating/own_def_rating
(team-level output, not player-level production) -- never fantasy_score
directly. This closes that gap: tests own pace/off/def rating and opponent
pace/off/def rating against fantasy_score in one pass, using
team_vs_opponent_trailing10 joined to game_fantasy_scores_weekly_lock_signal
on team_id+season_id+game_date. Read-only.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

N_BUCKETS = 5
MIN_GAMES = 10  # fully-populated trailing-10 windows only, apples-to-apples

METRICS = ["own_pace", "own_off_rating", "own_def_rating",
           "opp_pace", "opp_off_rating", "opp_def_rating"]

QUERY = """
    SELECT tvo.own_games_included,
           tvo.own_pace, tvo.own_off_rating, tvo.own_def_rating,
           tvo.opp_pace, tvo.opp_off_rating, tvo.opp_def_rating,
           gfsw.fantasy_score
    FROM team_vs_opponent_trailing10 tvo
    JOIN game_fantasy_scores_weekly_lock_signal gfsw
        ON gfsw.team_id = tvo.team_id
        AND gfsw.season_id = tvo.season_id
        AND gfsw.game_date = tvo.game_date
    WHERE gfsw.fantasy_score IS NOT NULL;
"""


def test_metric(df, metric):
    sub = df[[metric, "fantasy_score"]].dropna()
    corr = sub[metric].corr(sub["fantasy_score"])

    sub = sub.copy()
    sub["bucket"] = pd.qcut(sub[metric], q=N_BUCKETS, duplicates="drop")
    summary = sub.groupby("bucket", observed=True)["fantasy_score"].agg(
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
    print(f"n rows (games_included >= {MIN_GAMES}): {len(df)}")

    results = []
    for metric in METRICS:
        print()
        print("=" * 70)
        print(f"METRIC: {metric}  vs  fantasy_score")
        print("=" * 70)
        r = test_metric(df, metric)
        results.append(r)
        print(r["summary"].to_string())
        print(f"\n  Correlation: {r['corr']}   Monotonic: {r['monotonic']}   Swing: {r['swing']}")

    print()
    print("=" * 70)
    print("SUMMARY -- all six metrics vs fantasy_score, ranked by |swing|")
    print("=" * 70)
    ranked = sorted(results, key=lambda r: abs(r["swing"]), reverse=True)
    for r in ranked:
        print(f"  {r['metric']:16s} corr={r['corr']:+.4f}  monotonic={r['monotonic']}  swing={r['swing']:+.3f}")
    print()
    print("  fantasy_score has a stddev of ~14.5 (from the home/away test) -- use that as the")
    print("  yardstick for whether any swing here is worth a targeted backtest. Anything that")
    print("  clears that bar is a real candidate for the mechanism v2.0 hasn't built yet:")
    print("  something that adjusts fantasy_score expectation, sitting upstream of")
    print("  percentage_to_lock rather than trying to feed percentage_to_lock directly.")


if __name__ == "__main__":
    run()
