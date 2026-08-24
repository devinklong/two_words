"""
scripts/verify_player_scores_against_xlsx.py

For each row in the player_scores sheet (season, week_number, roster_id,
sleeper_player_id, points), checks whether that player actually posted
a fantasy_score matching (or close to) the recorded value in one of
their REAL games during that specific fantasy week.

Deliberately NOT a decision-engine correctness check -- game_lock_signal
scores against a perfect-hindsight oracle after the whole week has
played out, but real lock/hold decisions get made in real time as the
week unfolds (locking a player Tuesday, before Wednesday/Thursday's
games are known, can be the right real-time call even if a later game
that week scored higher). So this only asks the data-integrity
question: is the recorded points value a real score this player
actually put up during that week's games? Not: was it the optimal
choice.

RECORDED points=0 IS A LEGITIMATE OUTCOME, not a data error (confirmed
8/23/26): a real DNP after being locked in on a risky call (e.g. a
Sunday-questionable player who ends up not playing). game_fantasy_scores
only has rows for games actually played -- built from game_logs, which
comes from real box scores -- so a DNP produces NO row at all. That
means a recorded 0 can never be confirmed against game_fantasy_scores
the same way a nonzero score can; it's unverifiable by design, not
wrong. These rows are separated into their own bucket and NOT counted
as mismatches.

FIXED 8/23/26: 'BYE' and whitespace-padded 'NULL ' values weren't being
caught as sentinels before the crosswalk lookup, so they were falling
through and inflating the "unresolved crosswalk" bucket with what were
actually bracket-eliminated-roster placeholders, not real crosswalk
gaps. Both are now stripped/checked explicitly, same treatment as the
NULL literal.

Week date ranges come from fantasy_weeks (season_id, week_number ->
week_start_date/week_end_date). Player identity comes from
sleeper_player_crosswalk (sleeper_player_id -> nba_player_id). Real
per-game scores come from game_fantasy_scores -- which, as of 8/23/26,
correctly includes the real technical/flagrant foul penalty, so a
large share of what used to show up here as "mismatches" (the +2.00
cluster) should now resolve cleanly.

Usage: python scripts/verify_player_scores_against_xlsx.py <xlsx_path>
Example: python scripts/verify_player_scores_against_xlsx.py ~/Downloads/2024_2025_all_scores.xlsx
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

SENTINEL_VALUES = {"NULL", "BYE"}


def close_enough(a, b, tol=0.01):
    if a is None or b is None:
        return False
    return abs(round(float(a), 2) - round(float(b), 2)) < tol


def get_crosswalk(cur):
    cur.execute("SELECT sleeper_player_id, nba_player_id FROM sleeper_player_crosswalk;")
    return {sid: nid for sid, nid in cur.fetchall()}


def get_fantasy_weeks(cur):
    cur.execute("SELECT season_id, week_number, week_start_date, week_end_date FROM fantasy_weeks;")
    return {(season_id, week_number): (start, end) for season_id, week_number, start, end in cur.fetchall()}


def get_player_week_scores(cur, nba_player_id, start_date, end_date):
    cur.execute("""
        SELECT game_date, fantasy_score
        FROM game_fantasy_scores
        WHERE player_id = %s AND game_date BETWEEN %s AND %s
        ORDER BY game_date;
    """, (nba_player_id, start_date, end_date))
    return cur.fetchall()


def run(xlsx_path):
    df = pd.read_excel(xlsx_path, sheet_name="player_scores")
    df = df.dropna(subset=["season"])

    conn = get_connection()
    cur = conn.cursor()
    crosswalk = get_crosswalk(cur)
    weeks = get_fantasy_weeks(cur)

    matched = []
    mismatched = []
    no_games_that_week = []
    unresolved_crosswalk = []
    sentinel_rows = []
    recorded_zero_dnp = []

    for row in df.itertuples(index=False):
        season = int(row.season)
        week_number = int(row.week_number)
        roster_id = int(row.roster_id)
        sleeper_player_id = row.sleeper_player_id
        points = row.points
        season_id = f"2{season}"

        # Normalize and check sentinels FIRST, before any int/float
        # coercion -- 'BYE' and NULL-with-whitespace both need to be
        # caught here, not fall through to the crosswalk lookup.
        if pd.isna(sleeper_player_id):
            sid_str = None
        else:
            sid_str = str(sleeper_player_id).strip()

        if sid_str is None or sid_str.upper() in SENTINEL_VALUES:
            sentinel_rows.append((season, week_number, roster_id, sid_str, points))
            continue

        sleeper_player_id = str(int(float(sid_str))) if sid_str.replace(".", "", 1).isdigit() else sid_str
        nba_player_id = crosswalk.get(sleeper_player_id)
        if nba_player_id is None:
            unresolved_crosswalk.append((season, week_number, roster_id, sleeper_player_id, points))
            continue

        week_range = weeks.get((season_id, week_number))
        if week_range is None:
            print(f"WARNING: no fantasy_weeks row for season_id={season_id} week={week_number} -- skipping")
            continue
        start_date, end_date = week_range

        # Recorded 0 is a legitimate DNP outcome, not an error -- and
        # structurally unverifiable against game_fantasy_scores, since
        # a DNP produces no row there at all. Bucket separately,
        # skipped from the mismatch check entirely.
        if close_enough(points, 0):
            recorded_zero_dnp.append((season, week_number, roster_id, sleeper_player_id))
            continue

        games = get_player_week_scores(cur, nba_player_id, start_date, end_date)
        if not games:
            no_games_that_week.append((season, week_number, roster_id, sleeper_player_id, points))
            continue

        if any(close_enough(g_score, points) for _, g_score in games):
            matched.append((season, week_number, roster_id, sleeper_player_id, points))
        else:
            mismatched.append((season, week_number, roster_id, sleeper_player_id, points, games))

    cur.close()
    conn.close()

    total = len(df)
    print(f"{total} total player_scores rows.")
    print(f"{len(matched)} matched a real game score that week.")
    print(f"{len(mismatched)} had real games that week but NONE matched the recorded score.")
    print(f"{len(no_games_that_week)} had no real games at all that week (bench/DNP -- worth a look, not necessarily wrong).")
    print(f"{len(recorded_zero_dnp)} recorded as 0 -- presumed legitimate DNP after being locked, not checked (unverifiable by design).")
    print(f"{len(unresolved_crosswalk)} couldn't resolve sleeper_player_id via the crosswalk (real gaps, sentinels already excluded).")
    print(f"{len(sentinel_rows)} sentinel rows (BYE / NULL placeholders, skipped entirely).\n")

    if mismatched:
        print("=== Real mismatches (has games that week, none match) ===")
        print("season  week  roster_id  sleeper_id  recorded   real_scores_that_week")
        for season, week, roster_id, sid, points, games in sorted(mismatched):
            game_str = ", ".join(f"{d}: {s}" for d, s in games)
            print(f"{season:>6}  {week:>4}  {roster_id:>9}  {sid:>10}  {points:>8}   {game_str}")
        print()

    if no_games_that_week:
        print("=== No real games that week (season, week, roster_id, sleeper_id, recorded points) ===")
        for row in sorted(no_games_that_week):
            print(f"  {row}")
        print()

    if unresolved_crosswalk:
        print("=== Unresolved crosswalk -- real gaps (season, week, roster_id, sleeper_id, recorded points) ===")
        for row in sorted(unresolved_crosswalk):
            print(f"  {row}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/verify_player_scores_against_xlsx.py <xlsx_path>")
        sys.exit(1)
    run(sys.argv[1])
