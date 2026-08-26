"""
scripts/analysis/analyze_stacking_roster_construction.py

Follow-up to the confirmed real NBA-team stacking finding in
analyze_slot_covariance_residual_causes.py (stacked team-weeks show
real, meaningfully higher variance than non-stacked ones). That result
only showed stacking amplifies VOLATILITY -- it never said whether
that's good or bad. This answers the roster-construction questions
that naturally follow:

  - How many same-team players is "a stack" -- does win rate differ
    by stack DEGREE (0 shared, 2 sharing, 3+ sharing)?
  - Which position pairs actually get stacked together in this
    league's real rosters, and how often?

ASSUMPTION (same as analyze_slot_covariance_residual_causes.py, kept
consistent): each player's team is their MODE team_id for the season
(most games played for one team), not the exact team for the specific
locked game -- player_scores doesn't store which game produced a
locked score.

REAL CONFOUND FOUND AND CORRECTED (8/25/26): the first version compared
win rates pooled across all 10 rosters (40.1% stacked vs. 54.4% not,
p=0.0065) -- the exact same between-team-skill trap already found and
fixed in analyze_slot_covariance.py. If weaker managers happen to stack
more often, pooling makes stacking look worse than it really is,
independent of whether stacking itself does anything. Fix: compare each
ROSTER'S OWN win rate in its stacked weeks against that SAME roster's
win rate in its non-stacked weeks (a paired, within-roster comparison,
same principle as the demeaning fix elsewhere in this suite) -- this
cancels out each manager's overall skill level entirely. Both the raw
(confounded) and within-roster (corrected) results are printed so the
size of the confound is visible, not just the corrected number.

Win/loss and team-mode logic duplicated here rather than imported from
analyze_spike_locks_vs_wins.py / analyze_slot_covariance_residual_
causes.py -- matches this project's existing convention
(opponent_scout.py / waiver_wire_finder.py share get_spike_profile the
same way, intentionally copy-pasted, not imported).

Same Python-only convention as the rest of this suite -- single-pass
SQL, all math in pandas/numpy/scipy.
"""

import sys
from pathlib import Path
from collections import defaultdict, Counter
from itertools import combinations

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, wilcoxon

DEDICATED_SLOTS = ["PG", "SG", "SF", "PF", "C"]

LOCKED_QUERY = """
    SELECT league_id, week, roster_id, slot, nba_player_id, season
    FROM locked_scores_by_slot
    WHERE slot IN ('PG', 'SG', 'SF', 'PF', 'C')
      AND nba_player_id IS NOT NULL;
"""

MATCHUP_QUERY = """
    SELECT m.league_id, m.week, m.roster_id, m.matchup_id, spl.points
    FROM sleeper_matchups m
    JOIN sleeper_matchup_points_latest spl
        ON spl.league_id = m.league_id AND spl.week = m.week AND spl.roster_id = m.roster_id
    WHERE m.matchup_id IS NOT NULL;
"""

PLAYER_TEAM_QUERY = """
    SELECT player_id, season_id, team_id, COUNT(*) AS n_games
    FROM game_logs
    GROUP BY player_id, season_id, team_id;
"""


def fetch_locked_rows(cur):
    cur.execute(LOCKED_QUERY)
    return cur.fetchall()


def fetch_matchup_rows(cur):
    cur.execute(MATCHUP_QUERY)
    return cur.fetchall()


def fetch_player_team_counts(cur):
    cur.execute(PLAYER_TEAM_QUERY)
    return cur.fetchall()


def build_mode_team_per_player_season(team_count_rows):
    df = pd.DataFrame(team_count_rows, columns=["player_id", "season_id", "team_id", "n_games"])
    idx = df.groupby(["player_id", "season_id"])["n_games"].idxmax()
    return df.loc[idx].set_index(["player_id", "season_id"])["team_id"].to_dict()


def compute_win_loss(matchup_rows):
    by_matchup = defaultdict(list)
    for league_id, week, roster_id, matchup_id, points in matchup_rows:
        by_matchup[(league_id, week, matchup_id)].append((roster_id, float(points)))

    result = {}
    for (league_id, week, _), rosters in by_matchup.items():
        if len(rosters) != 2:
            continue
        (r1, p1), (r2, p2) = rosters
        if p1 == p2:
            result[(league_id, week, r1)] = "T"
            result[(league_id, week, r2)] = "T"
        elif p1 > p2:
            result[(league_id, week, r1)] = "W"
            result[(league_id, week, r2)] = "L"
        else:
            result[(league_id, week, r1)] = "L"
            result[(league_id, week, r2)] = "W"
    return result


def build_team_week_slot_teams(locked_rows, mode_teams):
    """One entry per (league_id, week, roster_id) -> {slot: team_id}."""
    by_key = defaultdict(dict)
    for league_id, week, roster_id, slot, nba_player_id, season in locked_rows:
        season_id = f"2{season}"
        team = mode_teams.get((nba_player_id, season_id))
        if team is not None:
            by_key[(league_id, week, roster_id)][slot] = team
    return by_key


def compute_stack_degree(slot_teams):
    """Largest number of dedicated slots sharing one real NBA team this
    team-week. 1 = no stacking (every slot on a different team, or only
    one slot present)."""
    if not slot_teams:
        return 1
    counts = Counter(slot_teams.values())
    return max(counts.values())


def find_stacked_pairs(slot_teams):
    """All (slotA, slotB) pairs sharing a team this team-week."""
    pairs = []
    for s1, s2 in combinations(sorted(slot_teams.keys()), 2):
        if slot_teams[s1] == slot_teams[s2]:
            pairs.append((s1, s2))
    return pairs


def run_win_rate_by_stack_degree(by_key, win_loss):
    print("\n=== Win rate by stack degree, RAW -- confounded by real between-team skill differences ===")
    buckets = defaultdict(lambda: {"W": 0, "L": 0})
    collapsed = defaultdict(lambda: {"W": 0, "L": 0})
    for key, slot_teams in by_key.items():
        result = win_loss.get(key)
        if result not in ("W", "L"):
            continue
        degree = compute_stack_degree(slot_teams)
        degree_label = "1 (no stack)" if degree == 1 else f"{degree} (stacked)"
        buckets[degree_label][result] += 1
        collapsed_label = "no stack" if degree == 1 else "stacked (2+)"
        collapsed[collapsed_label][result] += 1

    print(f"{'stack_degree':<16}{'n':>6}{'win%':>8}")
    for label in sorted(buckets.keys(), key=lambda k: int(k.split()[0])):
        b = buckets[label]
        n = b["W"] + b["L"]
        win_pct = 100.0 * b["W"] / n if n > 0 else float("nan")
        print(f"{label:<16}{n:>6}{win_pct:>7.1f}%")

    print("\n--- Collapsed to 2 groups (no stack vs. stacked 2+), RAW/confounded significance test ---")
    print(f"{'group':<16}{'n':>6}{'win%':>8}")
    table_rows = []
    for label in ("no stack", "stacked (2+)"):
        b = collapsed[label]
        n = b["W"] + b["L"]
        win_pct = 100.0 * b["W"] / n if n > 0 else float("nan")
        print(f"{label:<16}{n:>6}{win_pct:>7.1f}%")
        table_rows.append([b["W"], b["L"]])

    if all(sum(r) >= 5 for r in table_rows):
        chi2, p, _, _ = chi2_contingency(table_rows)
        verdict = "SIGNAL" if p < 0.05 else "noise"
        print(f"Chi-square, no-stack vs. stacked (RAW, confounded): chi2={chi2:.2f}  p={p:.4f}  {verdict}")
    else:
        print("Not enough data for a chi-square test even collapsed.")


def run_within_roster_stack_win_test(by_key, win_loss):
    """Corrected test: compares each roster's OWN win rate in stacked
    weeks against that SAME roster's win rate in non-stacked weeks --
    a paired, within-roster comparison that cancels out each manager's
    overall skill level entirely, same principle as the demeaning fix
    used elsewhere in this suite. Wilcoxon signed-rank test on the
    paired per-roster differences (non-parametric, appropriate for a
    small number of rosters and bounded proportions)."""
    print("\n=== Win rate by stacking, WITHIN-ROSTER -- corrected for team-quality confound ===")

    by_roster = defaultdict(lambda: {"stacked": {"W": 0, "L": 0}, "not": {"W": 0, "L": 0}})
    for key, slot_teams in by_key.items():
        league_id, week, roster_id = key
        result = win_loss.get(key)
        if result not in ("W", "L"):
            continue
        degree = compute_stack_degree(slot_teams)
        bucket = "stacked" if degree >= 2 else "not"
        by_roster[(league_id, roster_id)][bucket][result] += 1

    MIN_WEEKS_PER_GROUP = 3
    diffs = []
    print(f"{'league_id':<22}{'roster_id':>10}{'n_stacked':>11}{'win%_stacked':>14}{'n_not':>7}{'win%_not':>10}{'diff':>8}")
    for (league_id, roster_id), groups in sorted(by_roster.items()):
        n_stacked = groups["stacked"]["W"] + groups["stacked"]["L"]
        n_not = groups["not"]["W"] + groups["not"]["L"]
        if n_stacked < MIN_WEEKS_PER_GROUP or n_not < MIN_WEEKS_PER_GROUP:
            continue
        win_pct_stacked = 100.0 * groups["stacked"]["W"] / n_stacked
        win_pct_not = 100.0 * groups["not"]["W"] / n_not
        diff = win_pct_stacked - win_pct_not
        diffs.append(diff)
        print(f"{league_id:<22}{roster_id:>10}{n_stacked:>11}{win_pct_stacked:>13.1f}%{n_not:>7}{win_pct_not:>9.1f}%{diff:>+7.1f}%")

    print(f"\n{len(diffs)} roster-seasons with >={MIN_WEEKS_PER_GROUP} weeks in both groups.")
    if len(diffs) < 5:
        print("Not enough roster-seasons with both stacked and non-stacked weeks for a meaningful paired test.")
        return

    mean_diff = np.mean(diffs)
    stat, p = wilcoxon(diffs)
    verdict = "SIGNAL" if p < 0.05 else "noise"
    print(f"Mean within-roster win% difference (stacked - not stacked): {mean_diff:+.1f}%")
    print(f"Wilcoxon signed-rank test vs. 0: stat={stat:.2f}  p={p:.4f}  {verdict}")


def run_position_pair_frequency(by_key):
    print("\n=== Which position pairs actually get stacked together, and how often ===")
    pair_counts = Counter()
    total_team_weeks = len(by_key)
    for slot_teams in by_key.values():
        for pair in find_stacked_pairs(slot_teams):
            pair_counts[pair] += 1

    if not pair_counts:
        print("No stacked pairs found.")
        return

    print(f"{'pair':<10}{'n_team_weeks':>14}{'pct_of_all':>12}")
    for pair, count in sorted(pair_counts.items(), key=lambda kv: -kv[1]):
        pct = 100.0 * count / total_team_weeks
        print(f"{pair[0]}-{pair[1]:<7}{count:>14}{pct:>11.1f}%")


def run():
    conn = get_connection()
    cur = conn.cursor()
    locked_rows = fetch_locked_rows(cur)
    matchup_rows = fetch_matchup_rows(cur)
    team_count_rows = fetch_player_team_counts(cur)
    cur.close()
    conn.close()

    print(f"{len(locked_rows)} locked rows pulled.")
    print(f"{len(matchup_rows)} matchup-points rows pulled.")
    print(f"{len(team_count_rows)} player-season-team count rows pulled.")

    mode_teams = build_mode_team_per_player_season(team_count_rows)
    win_loss = compute_win_loss(matchup_rows)
    by_key = build_team_week_slot_teams(locked_rows, mode_teams)

    print(f"{len(by_key)} team-weeks with at least one team-identified dedicated-slot player.")

    run_win_rate_by_stack_degree(by_key, win_loss)
    run_within_roster_stack_win_test(by_key, win_loss)
    run_position_pair_frequency(by_key)


if __name__ == "__main__":
    run()
