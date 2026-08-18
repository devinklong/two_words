"""
scripts/analysis/home_away_vs_fantasy_score.py

Tests home/away against fantasy_score, not percentage_to_lock, since
percentage_to_lock only takes games_remaining_in_week + tier as inputs
and would hit the same confound the B2B test found. Confound check is
built in up front this time instead of discovered after the fact.
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
        gfsw.games_remaining_in_week,
        gl.is_home,
        gfsw.fantasy_score
    FROM game_fantasy_scores_weekly_percentage_to_lock gfsw
    JOIN game_logs gl
        ON gl.player_id = gfsw.player_id
        AND gl.season_id = gfsw.season_id
        AND gl.game_date = gfsw.game_date
    WHERE gfsw.fantasy_score IS NOT NULL
      AND gfsw.games_remaining_in_week IS NOT NULL;
"""
# ASSUMPTION: is_home lives on game_logs (player-level), joined by player_id+season_id+game_date.


def run():
    conn = get_connection()
    df = pd.read_sql(QUERY, conn)
    conn.close()

    home = df[df["is_home"] == True]
    away = df[df["is_home"] == False]

    print("=" * 70)
    print("STEP 1 -- EXPOSURE CHECK: is games_remaining_in_week distributed")
    print("the same way for home vs away games?")
    print("=" * 70)
    exposure = pd.DataFrame({
        "home_pct": home["games_remaining_in_week"].value_counts(normalize=True).sort_index(),
        "away_pct": away["games_remaining_in_week"].value_counts(normalize=True).sort_index(),
    }).round(4)
    exposure["diff"] = (exposure["home_pct"] - exposure["away_pct"]).round(4)
    print(exposure.to_string())
    print("\n  Large diffs would mean home/away skews toward particular points in the fantasy week.")

    print()
    print("=" * 70)
    print("STEP 2 -- OVERALL: home vs away, fantasy_score")
    print("=" * 70)
    overall = df.groupby("is_home")["fantasy_score"].agg(["count", "mean", "median", "std"]).round(3)
    print(overall.to_string())
    if True in overall.index and False in overall.index:
        naive_delta = overall.loc[True, "mean"] - overall.loc[False, "mean"]
        print(f"\n  Home minus away, fantasy_score: {naive_delta:+.3f}")

    print()
    print("=" * 70)
    print("STEP 3 -- BY TIER: does home/away matter more for some tiers than others?")
    print("=" * 70)
    by_tier = df.groupby(["tier", "is_home"])["fantasy_score"].agg(["count", "mean"]).round(3)
    print(by_tier.to_string())

    print()
    print("=" * 70)
    print("STEP 4 -- STRATIFIED (CONTROLLED) COMPARISON, for completeness")
    print("=" * 70)
    strata = []
    for grw in sorted(df["games_remaining_in_week"].unique()):
        h = home[home["games_remaining_in_week"] == grw]
        a = away[away["games_remaining_in_week"] == grw]
        if len(h) == 0 or len(a) == 0:
            continue
        delta = h["fantasy_score"].mean() - a["fantasy_score"].mean()
        weight = min(len(h), len(a))
        strata.append({"games_remaining_in_week": grw, "n_home": len(h), "n_away": len(a),
                        "delta_fantasy_score": round(delta, 3), "weight": weight})
    strata_df = pd.DataFrame(strata)
    print(strata_df.to_string(index=False))
    if len(strata_df) > 0 and strata_df["weight"].sum() > 0:
        controlled_delta = (strata_df["delta_fantasy_score"] * strata_df["weight"]).sum() / strata_df["weight"].sum()
        print(f"\n  Controlled delta (n-weighted across strata): {controlled_delta:+.3f}")
        print("  Should be close to the naive delta from Step 2 -- fantasy_score isn't computed from games_remaining_in_week.")


if __name__ == "__main__":
    run()
