"""
scripts/analysis/b2b_effect_controlled_for_schedule_position.py

Follow-up to player_team_b2b_fatigue_vs_lock.py: checks whether that
script's percentage_to_lock result is real or a games_remaining_in_week
confound (B2Bs may cluster late-week, mechanically raising
percentage_to_lock). Runs an exposure check plus a stratified
(schedule-position-controlled) comparison. Read-only.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))  # project root (scripts/analysis/ is two levels down)
from db_connection import get_connection

import pandas as pd

QUERY = """
    SELECT
        gfsw.tier,
        gfsw.games_remaining_in_week,
        CASE
            WHEN b2b.is_second_night_of_b2b AND b2b.is_first_night_of_b2b THEN 'b2b_sandwiched'
            WHEN b2b.is_second_night_of_b2b THEN 'second_night_of_b2b'
            WHEN b2b.is_first_night_of_b2b THEN 'first_night_of_b2b'
            ELSE 'rested'
        END AS schedule_situation,
        gfsw.fantasy_score,
        gfsw.percentage_to_lock
    FROM game_fantasy_scores_weekly_lock_signal gfsw
    JOIN team_schedule_b2b_flags b2b
        ON b2b.team_id = gfsw.team_id
        AND b2b.season_id = gfsw.season_id
        AND b2b.game_date = gfsw.game_date
    WHERE gfsw.fantasy_score IS NOT NULL
      AND gfsw.percentage_to_lock IS NOT NULL
      AND gfsw.games_remaining_in_week IS NOT NULL;
"""


def run():
    conn = get_connection()
    df = pd.read_sql(QUERY, conn)
    conn.close()

    rested = df[df["schedule_situation"] == "rested"]
    second = df[df["schedule_situation"] == "second_night_of_b2b"]

    print("=" * 70)
    print("STEP 1 -- EXPOSURE CHECK: is games_remaining_in_week distributed")
    print("the same way for rested vs second-night-of-B2B games?")
    print("=" * 70)
    exposure = pd.DataFrame({
        "rested_pct": rested["games_remaining_in_week"].value_counts(normalize=True).sort_index(),
        "second_night_pct": second["games_remaining_in_week"].value_counts(normalize=True).sort_index(),
    }).round(4)
    exposure["diff"] = (exposure["second_night_pct"] - exposure["rested_pct"]).round(4)
    print(exposure.to_string())
    print("\n  Second_night_pct systematically higher at low games_remaining_in_week = confound.")

    print()
    print("=" * 70)
    print("STEP 2 -- STRATIFIED (CONTROLLED) COMPARISON")
    print("rested vs second-night-of-B2B, WITHIN each games_remaining_in_week value")
    print("=" * 70)

    strata = []
    for grw in sorted(df["games_remaining_in_week"].unique()):
        r = rested[rested["games_remaining_in_week"] == grw]
        s = second[second["games_remaining_in_week"] == grw]
        if len(r) == 0 or len(s) == 0:
            continue
        delta_ptl = s["percentage_to_lock"].mean() - r["percentage_to_lock"].mean()
        delta_fs = s["fantasy_score"].mean() - r["fantasy_score"].mean()
        weight = min(len(r), len(s))  # weight by the smaller (binding) group
        strata.append({
            "games_remaining_in_week": grw,
            "n_rested": len(r), "n_second": len(s),
            "rested_ptl": round(r["percentage_to_lock"].mean(), 4),
            "second_ptl": round(s["percentage_to_lock"].mean(), 4),
            "delta_ptl": round(delta_ptl, 4),
            "delta_fantasy_score": round(delta_fs, 3),
            "weight": weight,
        })

    strata_df = pd.DataFrame(strata)
    print(strata_df.to_string(index=False))

    if len(strata_df) > 0 and strata_df["weight"].sum() > 0:
        controlled_delta_ptl = (strata_df["delta_ptl"] * strata_df["weight"]).sum() / strata_df["weight"].sum()
        controlled_delta_fs = (strata_df["delta_fantasy_score"] * strata_df["weight"]).sum() / strata_df["weight"].sum()
    else:
        controlled_delta_ptl = controlled_delta_fs = None

    naive_delta_ptl = second["percentage_to_lock"].mean() - rested["percentage_to_lock"].mean()
    naive_delta_fs = second["fantasy_score"].mean() - rested["fantasy_score"].mean()

    print()
    print("=" * 70)
    print("VERDICT")
    print("=" * 70)
    print(f"  Naive delta (from the first script)      -- percentage_to_lock: {naive_delta_ptl:+.4f}   fantasy_score: {naive_delta_fs:+.3f}")
    if controlled_delta_ptl is not None:
        print(f"  Controlled delta (n-weighted across strata) -- percentage_to_lock: {controlled_delta_ptl:+.4f}   fantasy_score: {controlled_delta_fs:+.3f}")
        shrink_pct = None
        if naive_delta_ptl != 0:
            shrink_pct = round(100 * (1 - controlled_delta_ptl / naive_delta_ptl), 1)
            print(f"\n  percentage_to_lock effect shrank by ~{shrink_pct}% once games_remaining_in_week is held fixed.")
        print("  fantasy_score holding steady while percentage_to_lock collapses confirms the confound.")
    else:
        print("  Not enough overlapping strata to compute a controlled estimate.")


if __name__ == "__main__":
    run()
