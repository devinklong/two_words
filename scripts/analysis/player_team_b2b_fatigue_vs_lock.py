"""
scripts/analysis/player_team_b2b_fatigue_vs_lock.py

Checks whether the player's own team being on a back-to-back moves
percentage_to_lock, split by four schedule situations (rested,
first/second night of B2B, and 3-in-3 sandwiched) and by tier, since
the existing 0.9805 multiplier is currently flat across tiers.
Read-only.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

QUERY = """
    SELECT
        gfsw.tier,
        CASE
            WHEN b2b.is_second_night_of_b2b AND b2b.is_first_night_of_b2b THEN 'b2b_sandwiched'
            WHEN b2b.is_second_night_of_b2b THEN 'second_night_of_b2b'
            WHEN b2b.is_first_night_of_b2b THEN 'first_night_of_b2b'
            ELSE 'rested'
        END AS schedule_situation,
        gfsw.fantasy_score,
        gfsw.percentage_to_lock
    FROM game_fantasy_scores_weekly_percentage_to_lock gfsw
    JOIN team_schedule_b2b_flags b2b
        ON b2b.team_id = gfsw.team_id
        AND b2b.season_id = gfsw.season_id
        AND b2b.game_date = gfsw.game_date
    WHERE gfsw.fantasy_score IS NOT NULL
      AND gfsw.percentage_to_lock IS NOT NULL;
"""

SITUATION_ORDER = ["rested", "first_night_of_b2b", "second_night_of_b2b", "b2b_sandwiched"]


def run():
    conn = get_connection()
    df = pd.read_sql(QUERY, conn)
    conn.close()

    print("=" * 70)
    print("OVERALL: schedule situation vs fantasy_score / percentage_to_lock")
    print("=" * 70)
    overall = df.groupby("schedule_situation").agg(
        n=("fantasy_score", "count"),
        avg_fantasy_score=("fantasy_score", "mean"),
        avg_percentage_to_lock=("percentage_to_lock", "mean"),
    ).round(4)
    overall = overall.reindex([s for s in SITUATION_ORDER if s in overall.index])
    print(overall.to_string())

    rested_fs = overall.loc["rested", "avg_fantasy_score"] if "rested" in overall.index else None
    rested_ptl = overall.loc["rested", "avg_percentage_to_lock"] if "rested" in overall.index else None
    if rested_fs is not None:
        print("\n  Delta vs rested baseline:")
        for sit in SITUATION_ORDER:
            if sit == "rested" or sit not in overall.index:
                continue
            fs_delta = overall.loc[sit, "avg_fantasy_score"] - rested_fs
            ptl_delta = overall.loc[sit, "avg_percentage_to_lock"] - rested_ptl
            print(f"    {sit}: fantasy_score {fs_delta:+.3f}  percentage_to_lock {ptl_delta:+.4f}")

    print()
    print("=" * 70)
    print("BY TIER: does the fatigue effect vary by player tier?")
    print("=" * 70)
    by_tier = df.groupby(["tier", "schedule_situation"]).agg(
        n=("fantasy_score", "count"),
        avg_fantasy_score=("fantasy_score", "mean"),
        avg_percentage_to_lock=("percentage_to_lock", "mean"),
    ).round(4)
    print(by_tier.to_string())

    print()
    print("=" * 70)
    print("Existing v1.1 multiplier (0.9805) is flat across tiers. If a tier's real")
    print("percentage_to_lock shift is meaningfully different, that's a case for a")
    print("tier-specific multiplier, pending a targeted backtest.")
    print("=" * 70)


if __name__ == "__main__":
    run()
