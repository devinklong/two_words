"""
scripts/analysis/analyze_spike_locks_vs_wins.py

The gap every other v3.2 script left open: everything so far measures
SCORING, never whether it translates to actually WINNING. Tests
whether a roster clearing its own lock_bar at a given slot in a given
week (a real "spike lock," using this project's real
GREATEST(35, mean + 0.5*stddev) formula) correlates with winning that
week's matchup -- per slot, not just Center, to see whether any
pattern found is slot-specific or general.

Win/loss determined from sleeper_matchups.matchup_id pairing two
rosters' sleeper_matchup_points_latest scores for the same
(league_id, week) -- a real matchup result, not team_scores in
isolation. Ties (equal points) excluded from the win-rate comparison,
kept in the printed counts for transparency.

ASSUMPTION flagged for review: assumes `player_season_fantasy_stats`
has `player_id`, `season_id`, `avg_fantasy_score`, `stddev_fantasy_score`
columns -- adjust PLAYER_SEASON_STATS_QUERY if the real schema differs.
lock_bar computed here directly (GREATEST(35, avg + 0.5*stddev)) rather
than calling the live `lock_bar()` Postgres function, to keep this
script fully self-contained and testable without a live-function
dependency.

Same Python-only convention as the rest of this suite -- single-pass
SQL, all math in numpy/scipy.
"""

import sys
from pathlib import Path
from collections import defaultdict

sys.path.append(str(Path(__file__).resolve().parents[1]))
from db_connection import get_connection

import numpy as np
from scipy.stats import fisher_exact

MIN_GROUP_SIZE = 5
LOCK_BAR_FLOOR = 35.0
LOCK_BAR_CEILING_MULT = 0.5

MATCHUP_QUERY = """
    SELECT m.league_id, m.week, m.roster_id, m.matchup_id, spl.points
    FROM sleeper_matchups m
    JOIN sleeper_matchup_points_latest spl
        ON spl.league_id = m.league_id AND spl.week = m.week AND spl.roster_id = m.roster_id
    WHERE m.matchup_id IS NOT NULL;
"""

LOCKED_QUERY = """
    SELECT league_id, season, week, roster_id, slot, points, nba_player_id
    FROM locked_scores_by_slot
    WHERE nba_player_id IS NOT NULL;
"""

PLAYER_SEASON_STATS_QUERY = """
    SELECT player_id, season_id, avg_fantasy_score, stddev_fantasy_score
    FROM player_season_fantasy_stats;
"""


def fetch_matchup_rows(cur):
    cur.execute(MATCHUP_QUERY)
    return cur.fetchall()


def fetch_locked_rows(cur):
    cur.execute(LOCKED_QUERY)
    return cur.fetchall()


def fetch_player_season_stats(cur):
    cur.execute(PLAYER_SEASON_STATS_QUERY)
    return {
        (player_id, season_id): (float(avg), float(stddev))
        for player_id, season_id, avg, stddev in cur.fetchall()
        if avg is not None and stddev is not None
    }


def compute_win_loss(matchup_rows):
    """Groups by (league_id, week, matchup_id), compares the two
    rosters' points, returns {(league_id, week, roster_id): 'W'/'L'/'T'}."""
    by_matchup = defaultdict(list)
    for league_id, week, roster_id, matchup_id, points in matchup_rows:
        by_matchup[(league_id, week, matchup_id)].append((roster_id, float(points)))

    result = {}
    for key, rosters in by_matchup.items():
        league_id, week, _ = key
        if len(rosters) != 2:
            continue  # bye/eliminated or malformed pairing, skip
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


def compute_lock_bar(avg, stddev):
    return max(LOCK_BAR_FLOOR, avg + LOCK_BAR_CEILING_MULT * stddev)


def build_spike_flags(locked_rows, player_stats):
    """For each locked row, determines whether it cleared that player's
    own lock_bar that season. Returns list of
    (league_id, week, roster_id, slot, cleared: bool)."""
    flags = []
    skipped = 0
    for league_id, season, week, roster_id, slot, points, nba_player_id in locked_rows:
        season_id = f"2{season}"
        stats = player_stats.get((nba_player_id, season_id))
        if stats is None:
            skipped += 1
            continue
        avg, stddev = stats
        bar = compute_lock_bar(avg, stddev)
        cleared = float(points) >= bar
        flags.append((league_id, week, roster_id, slot, cleared))
    return flags, skipped


def run_win_correlation(flags, win_loss, label):
    """Per slot: win rate when the slot cleared its spike bar that week
    vs. when it didn't. Fisher's exact test (appropriate for the
    modest sample sizes here) on the 2x2 win/loss-by-cleared table."""
    print(f"\n=== Spike-lock vs. win correlation, {label} ===")
    by_slot = defaultdict(lambda: {"cleared_w": 0, "cleared_l": 0, "not_w": 0, "not_l": 0})

    for league_id, week, roster_id, slot, cleared in flags:
        result = win_loss.get((league_id, week, roster_id))
        if result not in ("W", "L"):
            continue  # tie or no matchup result, excluded from win-rate test
        bucket = by_slot[slot]
        if cleared and result == "W":
            bucket["cleared_w"] += 1
        elif cleared and result == "L":
            bucket["cleared_l"] += 1
        elif not cleared and result == "W":
            bucket["not_w"] += 1
        else:
            bucket["not_l"] += 1

    print(f"{'slot':<6}{'n_cleared':>10}{'win%_cleared':>14}{'n_not':>8}{'win%_not':>10}{'p':>10}")
    for slot, b in sorted(by_slot.items()):
        n_cleared = b["cleared_w"] + b["cleared_l"]
        n_not = b["not_w"] + b["not_l"]
        if n_cleared < MIN_GROUP_SIZE or n_not < MIN_GROUP_SIZE:
            print(f"{slot:<6} not enough data in both groups, skipped")
            continue
        win_pct_cleared = 100.0 * b["cleared_w"] / n_cleared
        win_pct_not = 100.0 * b["not_w"] / n_not
        table = [[b["cleared_w"], b["cleared_l"]], [b["not_w"], b["not_l"]]]
        _, p = fisher_exact(table)
        verdict = "SIGNAL" if p < 0.05 else "noise"
        print(f"{slot:<6}{n_cleared:>10}{win_pct_cleared:>13.1f}%{n_not:>8}{win_pct_not:>9.1f}%{p:>10.4f}  {verdict}")


def run():
    conn = get_connection()
    cur = conn.cursor()
    matchup_rows = fetch_matchup_rows(cur)
    locked_rows = fetch_locked_rows(cur)
    player_stats = fetch_player_season_stats(cur)
    cur.close()
    conn.close()

    print(f"{len(matchup_rows)} matchup-points rows pulled.")
    print(f"{len(locked_rows)} locked rows pulled.")
    print(f"{len(player_stats)} player-season stat rows pulled.")

    win_loss = compute_win_loss(matchup_rows)
    flags, skipped = build_spike_flags(locked_rows, player_stats)
    if skipped:
        print(f"({skipped} locked row(s) skipped -- no matching player_season_fantasy_stats entry)")

    run_win_correlation(flags, win_loss, "all slots, both seasons pooled")


if __name__ == "__main__":
    run()
