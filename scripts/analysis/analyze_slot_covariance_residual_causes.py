"""
scripts/analysis/analyze_slot_covariance_residual_causes.py

Tests all 3 candidate explanations for the residual ~23% excess
team-week variance left over in analyze_slot_covariance.py after
removing the real roster-quality confound (season-long demeaning by
(league_id, roster_id)):

  (1) SHARED WEEK-LEVEL SCHEDULE DENSITY -- weeks with more total real
      NBA games give every slot more spike chances simultaneously.
      Tested via two-way (roster AND week) fixed-effects demeaning: if
      also removing each week's common effect collapses the remaining
      correlation, this is confirmed as (at least part of) the cause.
  (2) REAL NBA-TEAM STACKING -- slot pairs whose locked players
      happened to share a real NBA team that season should show more
      excess variance than pairs that never share a team, if this is a
      real contributor.
  (3) TIME-VARYING TEAM FORM -- season-long demeaning only removes a
      STATIC skill difference; a trailing rolling-window demean
      instead would also remove real mid-season form changes (trades,
      injury returns, hot/cold streaks). If switching from season-mean
      to rolling-mean demeaning shrinks the correlation further, real
      time-varying form was part of the residual.

ASSUMPTIONS flagged for review:
  - NBA-team stacking (2) uses each player's MODE team_id for the
    season (most games played for one team that season) from
    game_logs, not the exact team for the specific locked game --
    player_scores doesn't store which specific game produced a locked
    score, so exact per-game team context isn't available without a
    separate score-matching step (same kind used in
    verify_player_scores_against_xlsx.py). This is a real
    simplification for traded players, flagged rather than hidden.
  - Rolling-window demeaning (3) uses a trailing 4-week window,
    expanding for early-season weeks with less history. 4 is a
    reasonable default, not a validated choice -- adjust
    ROLLING_WINDOW_WEEKS if a different window is wanted.

Same Python-only convention as the rest of this suite -- single-pass
SQL, all math in pandas/numpy.
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
import pandas as pd

DEDICATED_SLOTS = ["PG", "SG", "SF", "PF", "C"]
ROLLING_WINDOW_WEEKS = 4

LOCKED_QUERY = """
    SELECT league_id, week, roster_id, slot, points, nba_player_id, season
    FROM locked_scores_by_slot
    WHERE slot IN ('PG', 'SG', 'SF', 'PF', 'C')
      AND nba_player_id IS NOT NULL;
"""

PLAYER_TEAM_QUERY = """
    SELECT player_id, season_id, team_id, COUNT(*) AS n_games
    FROM game_logs
    GROUP BY player_id, season_id, team_id;
"""


def fetch_locked_rows(cur):
    cur.execute(LOCKED_QUERY)
    return cur.fetchall()


def fetch_player_team_counts(cur):
    cur.execute(PLAYER_TEAM_QUERY)
    return cur.fetchall()


def build_mode_team_per_player_season(team_count_rows):
    """Picks each player's most-played team_id per season -- a real
    simplification for traded players, see docstring ASSUMPTION."""
    df = pd.DataFrame(team_count_rows, columns=["player_id", "season_id", "team_id", "n_games"])
    idx = df.groupby(["player_id", "season_id"])["n_games"].idxmax()
    mode_teams = df.loc[idx].set_index(["player_id", "season_id"])["team_id"]
    return mode_teams.to_dict()


def build_frames(rows):
    """Two wide frames sharing the same index: one of points (for
    correlation/variance), one of nba_player_id (for the stacking
    test)."""
    df = pd.DataFrame(rows, columns=["league_id", "week", "roster_id", "slot", "points", "nba_player_id", "season"])
    df["points"] = df["points"].astype(float)
    points_wide = df.pivot_table(index=["league_id", "week", "roster_id"], columns="slot", values="points", aggfunc="first")
    player_wide = df.pivot_table(index=["league_id", "week", "roster_id"], columns="slot", values="nba_player_id", aggfunc="first")
    season_by_key = df.groupby(["league_id", "week", "roster_id"])["season"].first()

    complete_idx = points_wide.dropna().index
    points = points_wide.loc[complete_idx, DEDICATED_SLOTS]
    players = player_wide.loc[complete_idx, DEDICATED_SLOTS]
    seasons = season_by_key.loc[complete_idx]
    return points, players, seasons


def print_excess_variance(data, label):
    individual_var_sum = data[DEDICATED_SLOTS].var(ddof=1).sum()
    real_total_var = data[DEDICATED_SLOTS].sum(axis=1).var(ddof=1)
    diff_pct = 100.0 * (real_total_var - individual_var_sum) / individual_var_sum if individual_var_sum > 0 else float("nan")
    print(f"{label}: sum_individual_var={individual_var_sum:.2f}  real_total_var={real_total_var:.2f}  excess={diff_pct:+.1f}%")


def print_corr_summary(data, label):
    corr = data[DEDICATED_SLOTS].corr()
    off_diag = corr.values[np.triu_indices(len(DEDICATED_SLOTS), k=1)]
    print(f"{label}: mean pairwise correlation={np.mean(off_diag):.3f}  (min={np.min(off_diag):.3f}, max={np.max(off_diag):.3f})")


def test_1_week_fixed_effects(points):
    """Two-way demeaning: remove BOTH roster season-mean AND week
    common-mean. If this collapses the correlation further than
    roster-only demeaning did, shared week-level factors (schedule
    density, or any other common week effect) explain part of the
    residual."""
    print("\n=== Test 1: shared week-level effects (two-way roster + week demeaning) ===")
    roster_demeaned = points.groupby(level=["league_id", "roster_id"]).transform(lambda c: c - c.mean())
    print_corr_summary(roster_demeaned, "Roster-only demeaned (baseline, matches analyze_slot_covariance.py)")
    print_excess_variance(roster_demeaned, "Roster-only demeaned (baseline)")

    two_way = roster_demeaned.groupby(level=["league_id", "week"]).transform(lambda c: c - c.mean())
    print_corr_summary(two_way, "Roster + week demeaned (removes shared week-level effects too)")
    print_excess_variance(two_way, "Roster + week demeaned")


def test_2_nba_team_stacking(points, players, mode_teams, seasons):
    """Flags each team-week as 'stacked' if 2+ of its 5 dedicated-slot
    players shared an NBA team that season (mode team, see docstring
    ASSUMPTION). Compares excess variance between stacked and
    non-stacked team-weeks, on roster-demeaned data (isolating real
    within-week effects, not roster quality)."""
    print("\n=== Test 2: real NBA-team stacking ===")
    roster_demeaned = points.groupby(level=["league_id", "roster_id"]).transform(lambda c: c - c.mean())

    stacked_flags = []
    for idx in players.index:
        season = seasons.loc[idx]
        season_id = f"2{season}"
        teams_this_row = []
        for slot in DEDICATED_SLOTS:
            player_id = players.loc[idx, slot]
            if pd.isna(player_id):
                continue
            team = mode_teams.get((int(player_id), season_id))
            if team is not None:
                teams_this_row.append(team)
        is_stacked = len(teams_this_row) != len(set(teams_this_row))
        stacked_flags.append(is_stacked)

    stacked_series = pd.Series(stacked_flags, index=players.index)
    n_stacked = stacked_series.sum()
    n_total = len(stacked_series)
    print(f"{n_stacked} of {n_total} team-weeks ({100.0 * n_stacked / n_total:.1f}%) have 2+ dedicated-slot players sharing an NBA team that season.")

    if n_stacked < 10 or (n_total - n_stacked) < 10:
        print("Not enough stacked or non-stacked team-weeks to compare meaningfully.")
        return

    print_excess_variance(roster_demeaned.loc[stacked_series], "Stacked team-weeks")
    print_excess_variance(roster_demeaned.loc[~stacked_series], "Non-stacked team-weeks")


def test_3_rolling_form(points):
    """Replaces season-long demeaning with a trailing rolling-window
    demean per roster -- removes time-varying team form (trades,
    injury returns, streaks), not just a static season-long skill
    level. If this shrinks correlation further than season demeaning,
    real time-varying form was part of the residual."""
    print(f"\n=== Test 3: time-varying team form (trailing {ROLLING_WINDOW_WEEKS}-week rolling demean) ===")

    rolled_frames = []
    for key, group in points.groupby(level=["league_id", "roster_id"]):
        g = group.reset_index(level=["league_id", "roster_id"], drop=True).sort_index()
        rolling_mean = g.rolling(window=ROLLING_WINDOW_WEEKS, min_periods=1).mean().shift(1)
        rolling_mean.iloc[0] = g.iloc[0]  # first week has no prior history; use its own value (demeans to 0, excluded in effect)
        demeaned = g - rolling_mean
        demeaned.index = pd.MultiIndex.from_tuples(
            [(key[0], w, key[1]) for w in g.index], names=["league_id", "week", "roster_id"]
        )
        rolled_frames.append(demeaned)

    rolling_demeaned = pd.concat(rolled_frames).dropna()
    print_corr_summary(rolling_demeaned, f"Rolling {ROLLING_WINDOW_WEEKS}-week demeaned")
    print_excess_variance(rolling_demeaned, f"Rolling {ROLLING_WINDOW_WEEKS}-week demeaned")


def run():
    conn = get_connection()
    cur = conn.cursor()
    locked_rows = fetch_locked_rows(cur)
    team_count_rows = fetch_player_team_counts(cur)
    cur.close()
    conn.close()

    print(f"{len(locked_rows)} locked rows pulled.")
    print(f"{len(team_count_rows)} player-season-team count rows pulled.")

    points, players, seasons = build_frames(locked_rows)
    print(f"{len(points)} complete team-weeks (all 5 dedicated slots filled).")

    if len(points) < 10:
        print("Not enough complete team-weeks for a meaningful test.")
        return

    mode_teams = build_mode_team_per_player_season(team_count_rows)

    test_1_week_fixed_effects(points)
    test_2_nba_team_stacking(points, players, mode_teams, seasons)
    test_3_rolling_form(points)


if __name__ == "__main__":
    run()
