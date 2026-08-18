"""
scripts/analysis/step5_opponent_defense_vs_lock.py

The step 5 test: does opponent def_rating (the strongest team-output
relationship found, corr 0.178-0.269 vs own_off_rating) actually move
percentage_to_lock? Runs both rolling windows side by side. Same
promote/reject bar as B2B/injury-return: a nonzero correlation isn't
enough without a meaningful bucketed swing. Read-only.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

N_BUCKETS = 5

WINDOWS = {
    "trailing_10": "team_vs_opponent_trailing10",
    "season_to_date": "team_vs_opponent_season_to_date",
}

QUERY_TEMPLATE = """
    SELECT tvo.own_games_included, tvo.opp_def_rating, gfsw.percentage_to_lock
    FROM {view} tvo
    JOIN game_fantasy_scores_weekly_percentage_to_lock gfsw
        ON gfsw.team_id = tvo.team_id
        AND gfsw.season_id = tvo.season_id
        AND gfsw.game_date = tvo.game_date
    WHERE tvo.opp_def_rating IS NOT NULL
      AND gfsw.percentage_to_lock IS NOT NULL;
"""

# trailing_10 caps at 10 games; season_to_date uses a floor of 15 instead.
MIN_GAMES = {"trailing_10": 10, "season_to_date": 15}


def run_window(conn, window_name, view_name):
    print("=" * 70)
    print(f"WINDOW: {window_name}  (view: {view_name})")
    print("=" * 70)

    df = pd.read_sql(QUERY_TEMPLATE.format(view=view_name), conn)
    min_games = MIN_GAMES[window_name]
    df = df[df["own_games_included"] >= min_games].copy()
    print(f"  n rows (games_included >= {min_games}): {len(df)}")

    corr = df["opp_def_rating"].corr(df["percentage_to_lock"])
    print(f"  Correlation (opp_def_rating, percentage_to_lock): {corr:.4f}")

    df["bucket"] = pd.qcut(df["opp_def_rating"], q=N_BUCKETS, duplicates="drop")
    summary = df.groupby("bucket", observed=True)["percentage_to_lock"].agg(
        ["count", "mean", "median", "std"]
    ).round(4)
    print("\n  Bucketed (opponent defense, worst to best):")
    print(summary.to_string())

    means = summary["mean"].tolist()
    diffs = [round(b - a, 4) for a, b in zip(means, means[1:])]
    monotonic = all(d >= 0 for d in diffs) or all(d <= 0 for d in diffs)
    total_swing = round(means[-1] - means[0], 4)

    print(f"\n  Bucket-to-bucket change: {diffs}")
    print(f"  Monotonic: {monotonic}")
    print(f"  Total swing (worst-defense bucket to best-defense bucket): {total_swing}")

    return {"window": window_name, "n": len(df), "corr": corr,
            "monotonic": monotonic, "total_swing": total_swing}


def run():
    conn = get_connection()
    results = [run_window(conn, name, view) for name, view in WINDOWS.items()]
    conn.close()

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    for r in results:
        print(f"  {r['window']}: corr={r['corr']:.4f}  monotonic={r['monotonic']}  "
              f"swing={r['total_swing']:.4f} (on a 0-1 scale)")
    print()
    print("  Same bar as B2B/injury-return: a small bucketed swing on percentage_to_lock")
    print("  is grounds to leave this alone, even though the off_rating relationship was strong.")
    print("  A meaningful swing is grounds for a targeted backtest before touching anything live.")


if __name__ == "__main__":
    run()
