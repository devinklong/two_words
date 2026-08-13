"""
scripts/analysis/bucketed_diagnostic.py

Cheap bucketed diagnostic (roadmap step 6): bins a predictor into
quantiles and checks how percentage_to_lock varies bucket to bucket,
catching non-linearity before it's built on. Read-only against
percentage_to_lock; joins to team_vs_opponent_trailing10 via
team_id+season_id+game_date. Edit BUCKET_VAR to change the predictor tested.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

N_BUCKETS = 5  # quintiles -- coarse enough to be readable, fine enough to catch non-linearity

BUCKET_VAR = "opp_def_rating"   # the team-level predictor being tested
BUCKET_TARGET = "percentage_to_lock"  # the real decision output

TARGET_QUERY = """
    SELECT tvo.own_games_included, tvo.{bucket_var}, gfsw.{bucket_target}
    FROM team_vs_opponent_trailing10 tvo
    JOIN game_fantasy_scores_weekly_lock_signal gfsw
        ON gfsw.team_id = tvo.team_id
        AND gfsw.season_id = tvo.season_id
        AND gfsw.game_date = tvo.game_date
    WHERE tvo.own_games_included = 10   -- fully-populated windows only, apples-to-apples
      AND tvo.{bucket_var} IS NOT NULL
      AND gfsw.{bucket_target} IS NOT NULL;
"""


def run():
    conn = get_connection()
    query = TARGET_QUERY.format(bucket_var=BUCKET_VAR, bucket_target=BUCKET_TARGET)
    df = pd.read_sql(query, conn)
    conn.close()

    print("=" * 70)
    print(f"BUCKETED DIAGNOSTIC: {BUCKET_VAR} (binned) vs. {BUCKET_TARGET}")
    print("=" * 70)
    print(f"  n rows: {len(df)}  (player-games, many-to-one against team-games)")

    df["bucket"] = pd.qcut(df[BUCKET_VAR], q=N_BUCKETS, duplicates="drop")

    summary = df.groupby("bucket", observed=True)[BUCKET_TARGET].agg(
        ["count", "mean", "median", "std"]
    ).round(4)
    print(summary.to_string())

    # Checks whether the target is monotonic across buckets or rises-then-falls.
    means = summary["mean"].tolist()
    diffs = [round(b - a, 4) for a, b in zip(means, means[1:])]
    monotonic = all(d >= 0 for d in diffs) or all(d <= 0 for d in diffs)

    print(f"\n  Bucket-to-bucket change in mean: {diffs}")
    print(f"  Monotonic across buckets: {monotonic}")
    if not monotonic:
        print("  --> Non-monotonic. A straight-line/linear relationship would miss "
              "this -- worth a closer look at which bucket breaks the pattern before "
              "wiring this into anything.")

    if monotonic and len(diffs) > 0:
        total_swing = round(means[-1] - means[0], 4)
        print(f"\n  Total swing from lowest to highest bucket: {total_swing} "
              f"({'increases' if total_swing > 0 else 'decreases'} with {BUCKET_VAR})")
        print("  Small swing (near 0) even if monotonic = probably not worth promoting "
              "into the decision -- same bar B2B/injury-return were held to.")


if __name__ == "__main__":
    run()
