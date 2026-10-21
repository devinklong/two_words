"""
scripts/ingestion/load_season_schedule.py

Loads the regular-season schedule (including games not yet played) into
team_schedule, two rows per game (home + away), with wl/pts/plus_minus
left NULL. Added 10/21/26.

WHY: load_daily_team_schedule.py only ever returns games that have
already been played (verified 10/21/26: a future-date range inserted 0
rows). The weekly "games remaining" columns -- including
effective_games_remaining_in_week, which the hold-value curve depends on
-- count a team's LATER games in the same week from team_schedule. In a
live season those games don't exist there yet, and
sync_game_fantasy_scores_weekly_effective copies the counts at insert
time and never updates them. Historical seasons worked because the whole
season was backfilled at once.

Source: nba_api ScheduleLeagueV2 (one call for the whole season).

WHAT THE FEED ACTUALLY CONTAINS (diagnosed 10/21/26, --diagnose):
  * Every team has exactly 80 published regular-season games, not 82
    (1,200 games). The remaining 30 games are NBA Cup (Emirates NBA Cup)
    knockout-round games whose teams aren't known until group play ends
    (late Nov): 6 appear as TBD placeholders (team id 0, gameId
    0022601201-04, 1229-30, Dec 4-8) and 24 aren't in the feed yet.
  * Preseason (gameId 001...) and the Cup final (006..., doesn't count
    toward the regular season) are in the feed too. Both are excluded.
So: placeholder games (a team id that isn't one of the 30 franchises) are
SKIPPED, and this script is meant to be re-run daily -- the NBA fills in
those games over time and each run picks up whatever is new. Weeks
before December are fully known now.

Safety:
  * DRY RUN by default; --write inserts.
  * Sanity gate before any write: all 30 teams present, every team has
    the SAME number of games, and that number is 78-82 (80 expected now;
    82 once the Cup games are published). Anything else exits without
    writing (override only with --force).
  * Regular season only: gameId prefix '002'.
  * Insert is ON CONFLICT (game_id, team_id) DO UPDATE ... WHERE
    team_schedule.wl IS NULL, i.e. it can move a not-yet-played game
    (postponement) but never touches a row with a recorded result.
    Rows already loaded by the daily loader (with results) are untouched.
  * Idempotent: safe to run every morning (run_daily.py does).
  * Nothing reads team_schedule.wl/pts (grep'd 10/21/26), so rows loaded
    here that are later played keep NULL results harmlessly.

Run (from project root):
    python scripts/ingestion/load_season_schedule.py --diagnose
    python scripts/ingestion/load_season_schedule.py            (dry run)
    python scripts/ingestion/load_season_schedule.py --write
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

REAL_TEAM_IDS = set(range(1610612737, 1610612767))  # the 30 NBA franchises
FULL_SEASON_PER_TEAM = 82
MIN_PER_TEAM = 78  # below this something is wrong, not just "Cup games not published yet"


def find_col(cols, patterns):
    for pat in patterns:
        for c in cols:
            if re.fullmatch(pat, c, re.IGNORECASE):
                return c
    return None


def build_rows(df, season_id):
    """Returns (rows, problems, info). rows = [(game_id, season_id, team_id, opp_id, date, is_home)].
    Games where either team is not one of the 30 franchises (TBD placeholders) are skipped and counted."""
    import pandas as pd

    cols = list(df.columns)
    gid = find_col(cols, [r"gameId"])
    date_col = find_col(cols, [r"gameDateEst", r"gameDate"])
    home = find_col(cols, [r"home.*team.*id", r"hometeamid"])
    away = find_col(cols, [r"away.*team.*id", r"awayteamid"])
    problems = []
    for name, c in (("gameId", gid), ("date", date_col), ("home team id", home), ("away team id", away)):
        if c is None:
            problems.append(f"could not find a {name} column")
    if problems:
        return None, problems, {}

    reg = df[df[gid].astype(str).str.startswith("002")].copy()
    reg["game_dt"] = pd.to_datetime(reg[date_col], errors="coerce").dt.date
    if reg["game_dt"].isna().any():
        problems.append(f"{int(reg['game_dt'].isna().sum())} games with an unparseable date")

    rows, placeholders = [], 0
    for game_id, d, h, a in zip(reg[gid].astype(str), reg["game_dt"], reg[home].astype(int), reg[away].astype(int)):
        if h not in REAL_TEAM_IDS or a not in REAL_TEAM_IDS:
            placeholders += 1
            continue
        rows.append((game_id, season_id, int(h), int(a), d, True))
        rows.append((game_id, season_id, int(a), int(h), d, False))
    return rows, problems, {"placeholders": placeholders, "regular_rows": len(reg)}


def sanity(rows):
    """Returns (problems, per_team_count_or_None). Empty problems = passes the gate."""
    problems = []
    per_team = Counter(r[2] for r in rows)
    if len(per_team) != 30:
        problems.append(f"{len(per_team)} distinct teams (expected 30)")
    counts = set(per_team.values())
    n = None
    if len(counts) != 1:
        problems.append(f"teams have different game counts: {dict(Counter(per_team.values()))}")
    else:
        n = next(iter(counts))
        if not (MIN_PER_TEAM <= n <= FULL_SEASON_PER_TEAM):
            problems.append(f"{n} games per team (expected {MIN_PER_TEAM}-{FULL_SEASON_PER_TEAM})")
    return problems, n


def diagnose(df):
    """Print where the schedule departs from '1230 games, 82 per team, 30 teams'."""
    cols = list(df.columns)
    gid = find_col(cols, [r"gameId"])
    home = find_col(cols, [r"home.*team.*id", r"hometeamid"])
    away = find_col(cols, [r"away.*team.*id", r"awayteamid"])
    date_col = find_col(cols, [r"gameDateEst", r"gameDate"])
    show = [c for c in cols if re.fullmatch(r"gameLabel|gameSubLabel|gameSubtype|seriesText|postponedStatus|gameStatusText", c, re.IGNORECASE)]
    names = [c for c in cols if re.fullmatch(r"(home|away)Team_team(Name|Tricode|City)", c, re.IGNORECASE)]

    print("\n--- gameId prefix counts (002=regular season) ---")
    print(df[gid].astype(str).str[:3].value_counts().to_string())
    for c in show:
        print(f"\n--- {c}: value counts ---")
        print(df[c].astype(str).value_counts().head(12).to_string())

    odd = df[~df[home].astype(int).isin(REAL_TEAM_IDS) | ~df[away].astype(int).isin(REAL_TEAM_IDS)]
    print(f"\n--- games with a team id outside the 30 real franchises: {len(odd)} ---")
    print(odd[[gid, date_col, home, away, *names, *show]].head(15).to_string())
    print("  odd id values:", sorted((set(odd[home].astype(int)) | set(odd[away].astype(int))) - REAL_TEAM_IDS)[:10])

    reg = df[df[gid].astype(str).str.startswith("002")]
    counts = Counter()
    for h, a in zip(reg[home].astype(int), reg[away].astype(int)):
        if h in REAL_TEAM_IDS and a in REAL_TEAM_IDS:
            counts[h] += 1
            counts[a] += 1
    print("\n--- regular-season (002) games per team, real-vs-real only: how many teams have each count ---")
    print(dict(sorted(Counter(counts[t] for t in REAL_TEAM_IDS if t in counts).items())))
    print("  real teams with no 002 games at all:", [t for t in REAL_TEAM_IDS if t not in counts])
    non_reg = df[~df[gid].astype(str).str.startswith("002")]
    print(f"\n--- non-regular-season rows: {len(non_reg)} ---")
    print(non_reg[[gid, date_col, *names, *show]].head(10).to_string())


def main():
    ap = argparse.ArgumentParser(description="Load the season schedule into team_schedule.")
    ap.add_argument("--season", default="2026-27", help="nba_api season string, e.g. 2026-27")
    ap.add_argument("--write", action="store_true", help="insert (default is a dry run)")
    ap.add_argument("--force", action="store_true", help="write even if the sanity gate fails")
    ap.add_argument("--inspect", action="store_true", help="print the schedule columns and exit")
    ap.add_argument("--diagnose", action="store_true", help="explain any departure from 82 games per team / 30 teams, then exit")
    args = ap.parse_args()

    season_id = "2" + args.season[:4]  # '2026-27' -> '22026'

    from nba_api.stats.endpoints import scheduleleaguev2
    frames = scheduleleaguev2.ScheduleLeagueV2(season=args.season, timeout=60).get_data_frames()
    df = next((f for f in frames if "gameId" in f.columns), None)
    if df is None:
        sys.exit(f"No dataframe with a gameId column. Frames: {[list(f.columns)[:6] for f in frames]}")
    print(f"Pulled {len(df)} schedule rows for {args.season} (season_id {season_id}).")

    if args.diagnose:
        diagnose(df)
        return
    if args.inspect:
        print("Columns:", list(df.columns))
        print(df.head(3).to_string())
        return

    rows, problems, info = build_rows(df, season_id)
    if rows is None:
        print("Column detection failed:", "; ".join(problems))
        print("Columns:", list(df.columns))
        sys.exit(1)
    gate_problems, per_team = sanity(rows)
    problems += gate_problems

    games = len(rows) // 2
    dates = sorted({r[4] for r in rows if r[4]})
    print(f"Regular season: {games} games with known teams ({len(rows)} team-rows), {dates[0]} to {dates[-1]}.")
    if info.get("placeholders"):
        print(f"Skipped {info['placeholders']} placeholder game(s) with TBD teams (NBA Cup knockout); "
              f"they are picked up on a later run once the teams are known.")
    if per_team is not None and per_team < FULL_SEASON_PER_TEAM:
        print(f"NOTE: {per_team} games per team published so far, not {FULL_SEASON_PER_TEAM} "
              f"({(FULL_SEASON_PER_TEAM - per_team) * 30 // 2} games not scheduled yet). "
              f"Re-run daily; the rest appear after NBA Cup group play (late Nov).")

    if problems:
        print("SANITY GATE FAILED:")
        for p in problems:
            print("  -", p)
        if not args.force:
            print("Nothing written. Fix the cause (or re-run with --force if you have checked it).")
            sys.exit(1)
        print("--force given: continuing anyway.")

    if not args.write:
        print("Dry run: nothing written. Re-run with --write.")
        return

    from psycopg2.extras import execute_values
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT team_id FROM teams")
    known = {r[0] for r in cur.fetchall()}
    unknown = {r[2] for r in rows} - known
    if unknown:
        conn.close()
        sys.exit(f"team ids not in teams table: {sorted(unknown)} -- nothing written.")

    cur.execute("SELECT COUNT(*) FROM team_schedule WHERE season_id = %s", (season_id,))
    before = cur.fetchone()[0]
    execute_values(cur, """
        INSERT INTO team_schedule (game_id, season_id, team_id, opponent_team_id, game_date, is_home)
        VALUES %s
        ON CONFLICT (game_id, team_id) DO UPDATE
            SET game_date = EXCLUDED.game_date,
                opponent_team_id = EXCLUDED.opponent_team_id,
                is_home = EXCLUDED.is_home
            WHERE team_schedule.wl IS NULL
    """, rows)
    conn.commit()
    cur.execute("SELECT COUNT(*), COUNT(*) FILTER (WHERE wl IS NULL) FROM team_schedule WHERE season_id = %s", (season_id,))
    after, unplayed = cur.fetchone()
    cur.close()
    conn.close()
    print(f"team_schedule {season_id}: {before} -> {after} rows ({after - before} new); "
          f"{unplayed} unplayed (wl NULL), {after - unplayed} with results.")


if __name__ == "__main__":
    main()
