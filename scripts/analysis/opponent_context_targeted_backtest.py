"""
scripts/analysis/opponent_context_targeted_backtest.py

Targeted backtest for opp_def_rating and opp_pace, the two metrics that
survived the within-player confound check. Restricted to "close call"
games -- fantasy_score within BAND_POINTS of the player's own lock bar,
GREATEST(35, running_mean + 0.5*running_stddev) computed from PRIOR games
only (no leakage) -- since those are the only decisions this signal could
actually change, same principle as the injury-return backtest. Tests
whether a favorable matchup (weak opponent defense / fast opponent pace)
predicts clearing the bar more often than a 50% coinflip. Read-only.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

MIN_GAMES = 10       # fully-populated trailing-10 windows only
MIN_PRIOR_GAMES = 20  # running mean/stddev needs enough prior games to be stable
BAND_POINTS = 3.0    # "close call" = within this many fantasy_score points of the bar

QUERY = """
    SELECT gfsw.player_id, gfsw.game_date, tvo.own_games_included,
           tvo.opp_def_rating, tvo.opp_pace,
           gfsw.fantasy_score
    FROM team_vs_opponent_trailing10 tvo
    JOIN game_fantasy_scores_weekly_lock_signal gfsw
        ON gfsw.team_id = tvo.team_id
        AND gfsw.season_id = tvo.season_id
        AND gfsw.game_date = tvo.game_date
    WHERE gfsw.fantasy_score IS NOT NULL;
"""


def add_running_lock_bar(df):
    """Running mean/stddev computed from PRIOR games only (shift before expanding), matching
    how the live system would see history at decision time -- no leakage from the current game."""
    df = df.sort_values(["player_id", "game_date"]).copy()
    grouped = df.groupby("player_id")["fantasy_score"]
    df["prior_count"] = grouped.transform(lambda s: s.shift(1).expanding().count())
    df["running_mean"] = grouped.transform(lambda s: s.shift(1).expanding().mean())
    df["running_std"] = grouped.transform(lambda s: s.shift(1).expanding().std())
    df["lock_bar"] = df[["running_mean"]].assign(
        bar=lambda x: (x["running_mean"] + 0.5 * df["running_std"]).clip(lower=35)
    )["bar"]
    return df


def backtest_metric(df, metric, favorable_direction):
    """favorable_direction: 'high' if a higher metric value should predict clearing the bar,
    'low' if a lower value should."""
    sub = df.dropna(subset=[metric, "lock_bar"]).copy()
    sub["close_call"] = (sub["fantasy_score"] - sub["lock_bar"]).abs() <= BAND_POINTS
    close = sub[sub["close_call"]].copy()

    median_val = close[metric].median()
    if favorable_direction == "high":
        close["favorable_matchup"] = close[metric] >= median_val
    else:
        close["favorable_matchup"] = close[metric] <= median_val

    close["actually_cleared_bar"] = close["fantasy_score"] >= close["lock_bar"]
    close["prediction_correct"] = close["favorable_matchup"] == close["actually_cleared_bar"]

    n = len(close)
    accuracy = close["prediction_correct"].mean() if n > 0 else None
    return n, accuracy, close


def run():
    conn = get_connection()
    df = pd.read_sql(QUERY, conn)
    conn.close()

    df = df[df["own_games_included"] >= MIN_GAMES].copy()
    df = add_running_lock_bar(df)
    df = df[df["prior_count"] >= MIN_PRIOR_GAMES].copy()
    print(f"n rows eligible (games_included >= {MIN_GAMES}, prior_count >= {MIN_PRIOR_GAMES}): {len(df)}")

    for metric, direction in [("opp_def_rating", "high"), ("opp_pace", "high")]:
        print()
        print("=" * 70)
        print(f"BACKTEST: {metric}  (favorable = {direction} value predicts clearing the bar)")
        print("=" * 70)
        n, accuracy, close = backtest_metric(df, metric, direction)
        print(f"  Close-call games (within {BAND_POINTS} pts of lock bar): {n}")
        if accuracy is not None:
            print(f"  Prediction accuracy: {accuracy:.1%}  (coinflip baseline: 50.0%)")
            print(f"  Edge over coinflip: {(accuracy - 0.5) * 100:+.1f} points")
            if accuracy > 0.5:
                print("  Above coinflip -- candidate for wiring in, pending review of the edge size.")
            else:
                print("  At or below coinflip -- same outcome as injury-return: correctly reject this signal.")
        else:
            print("  No close-call games found -- widen BAND_POINTS or check MIN_PRIOR_GAMES.")

    print()
    print("=" * 70)
    print("Same bar as injury-return: an edge that doesn't clear 50% on the specific decisions")
    print("it could change means don't wire it in, regardless of how strong the broader")
    print("correlation looked. Only a real edge here justifies touching percentage_to_lock.")


if __name__ == "__main__":
    run()
