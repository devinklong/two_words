"""
scripts/run_daily.py

One-command morning routine for the live season. Runs each daily step in
order as its own subprocess, stops at the first failure (so a broken load
can't silently feed the next step), logs all output to
scripts/logs/daily_<date>.log, and finishes with the independent verifier
and the preflight so there is a single pass/fail summary.

Steps, in order (the ORDER MATTERS -- see docs/daily_runbook.md):
  0. load_season_schedule.py --write       (full season incl. UPCOMING games; idempotent; must precede game logs)
  1. load_daily_team_schedule.py           (played games with results; must precede game logs)
  1b. check_schedule_growth.py             (log only: warns when the NBA schedule grew, e.g. Cup games -> December rebuild)
  2. REFRESH team_schedule_b2b_flags       (matview; the weekly-effective sync reads it and never updates rows it already wrote)
  3. fantasy_weeks.sql                     (additive; no-op unless a season is missing)
  4. load_players.py                       (offline; game_logs.player_id has a FK to nba_players again, so unknown players must exist first)
  5. load_daily_game_logs.py <date>        (chains weekly-effective sync + gap_reasons/injuries; a game that fails on an
                                           unknown rookie is healed via add_player_by_id.py and the loader re-run)
  6. backfill_missing_players.py           (no-op unless game_logs has unknown players)
  7. build_sleeper_player_crosswalk.py     (only if rostered players still lack an nba_player_id)
  8. run_daily_sync.py                     (rosters, matchups, transactions, points snapshots)
  9. verify_current_league_independently.py --week W
 10. current-team snapshot (nba_api, feeds the schedule columns); then lock-signal check, FAAB balances, waiver_wire_finder, waiver_bid_advisor, my_lock_report and opponent_scout
     (roster MY_ROSTER_ID) smoke tests (output goes to the log)
 11. season_opener_preflight.py --week W

<date> defaults to YESTERDAY in America/Chicago: run this in the morning
and it loads last night's games. W is the current fantasy week, looked up
in fantasy_weeks for today.

ASSUMPTION (not confirmed): load_daily_team_schedule.py takes no arguments.
Run with --dry-run first, and check that script's header.

Run (from project root):
    python scripts/run_daily.py --dry-run         # print the plan, run nothing
    python scripts/run_daily.py                   # normal morning run
    python scripts/run_daily.py --date 2026-10-20 # load a specific game day
    python scripts/run_daily.py --keep-going      # don't stop at a failure
    python scripts/run_daily.py --backup          # pg_dump to ~/backups first
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LOG_DIR = SCRIPTS / "logs"
PY = sys.executable
MY_ROSTER_ID = 8  # sweetdiddlydee -- used by the opponent_scout smoke test
TZ = ZoneInfo("America/Chicago")
# Steps that call stats.nba.com get one retry after a pause (it times out under load).
RETRY_PAUSE_SECONDS = 60


def current_week(today):
    """(season_id, week_number or None) for the live season."""
    sys.path.append(str(SCRIPTS))
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT season_id FROM current_season_config ORDER BY updated_at DESC LIMIT 1")
    season_id = cur.fetchone()[0]
    cur.execute("SELECT week_number FROM fantasy_weeks WHERE season_id = %s AND %s BETWEEN week_start_date AND week_end_date",
                (season_id, today))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return season_id, (row[0] if row else None)


def unmatched_rostered_count():
    sys.path.append(str(SCRIPTS))
    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM roster_ownership WHERE nba_player_id IS NULL")
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n


def build_steps(game_date, season_id, week, run_crosswalk):
    wk = ["--week", str(week)] if week else ["--week", "1"]
    psql = ["psql", "-d", "postgres"]
    return [
        ("season schedule (full, incl. upcoming)", [PY, "scripts/ingestion/load_season_schedule.py", "--write"], True),
        ("team schedule (played games)", [PY, "scripts/ingestion/load_daily_team_schedule.py"], True),
        ("schedule growth check (in log)", [PY, "scripts/check_schedule_growth.py", "--season-id", str(season_id)], False),
        ("refresh b2b flags", [*psql, "-c", "REFRESH MATERIALIZED VIEW team_schedule_b2b_flags"], False),
        ("fantasy_weeks (additive)", [*psql, "-f", "schema/tables/fantasy_weeks.sql"], False),
        ("load players (static list)", [PY, "scripts/ingestion/load_players.py"], False),
        ("game logs", [PY, "scripts/ingestion/load_daily_game_logs.py", game_date], True),
        ("backfill missing players", [PY, "scripts/ingestion/backfill_missing_players.py"], True),
        ("technical/flagrant foul scan", [PY, "scripts/backfill_technical_flagrant_fouls.py", "--season-id", str(season_id)], True),
        ("apply foul penalties to stored scores", [*psql, "-c",
            "UPDATE game_fantasy_scores_weekly_effective t SET fantasy_score = f.fantasy_score "
            "FROM game_fantasy_scores_weekly_full f WHERE t.game_id = f.game_id AND t.player_id = f.player_id "
            f"AND t.season_id = '{season_id}' AND t.fantasy_score IS DISTINCT FROM f.fantasy_score"], True),
        *([("sleeper crosswalk", [PY, "scripts/sleeper/build_sleeper_player_crosswalk.py"], False)] if run_crosswalk else []),
        ("sleeper sync", [PY, "scripts/sleeper/run_daily_sync.py"], False),
        ("table nba_lock_decisions (additive)", [*psql, "-f", "schema/tables/nba_lock_decisions.sql"], False),
        ("infer locks from sleeper snapshots", [PY, "scripts/infer_lock_decisions.py", "--season-id", str(season_id)], False),
        ("independent verify", [PY, "scripts/verify_current_league_independently.py", *wk], False),
        ("current teams table", [*psql, "-f", "schema/tables/nba_player_current_team.sql"], False),
        ("current teams (nba_api snapshot)", [PY, "scripts/ingestion/load_current_teams.py", "--write"], True),
        ("lock signal check (in log)", [*psql, "-c", "SELECT player_stats_source, lock_signal, COUNT(*) FROM game_lock_signal "
                                        f"WHERE season_id = '{season_id}' GROUP BY 1,2 ORDER BY 1,2"], False),
        ("faab balances (in log)", [*psql, "-c", "SELECT roster_id, owner_name, budget, budget_used, remaining, other_budget_movement "
                                    f"FROM sleeper_faab_balance WHERE season = '{str(season_id)[1:]}' ORDER BY roster_id"], False),
        ("waiver finder smoke (in log)", [PY, "scripts/waiver_wire_finder.py", "--season-id", str(season_id), "--limit", "5"], False),
        ("bid advisor smoke (in log)", [PY, "scripts/waiver_bid_advisor.py", "--season-id", str(season_id), "--limit", "5"], False),
        ("my lock report smoke (in log)", [PY, "scripts/my_lock_report.py", "--roster-id", str(MY_ROSTER_ID), "--season-id", str(season_id)], False),
        ("opponent scout smoke (in log)", [PY, "scripts/opponent_scout.py", str(MY_ROSTER_ID), *wk, "--season-id", str(season_id)], False),
        ("preflight", [PY, "scripts/season_opener_preflight.py", *wk], False),
    ]


FK_MISSING_PLAYER = re.compile(r"Key \(player_id\)=\((\d+)\) is not present in table \"nba_players\"")
MAX_HEAL_ROUNDS = 5
LITE_STEPS = {"game logs", "backfill missing players", "technical/flagrant foul scan", "apply foul penalties to stored scores",
              "sleeper crosswalk", "sleeper sync", "table nba_lock_decisions (additive)", "infer locks from sleeper snapshots"}


def run_once(cmd, log):
    """Run a command, tee its output into the log, return (returncode, output)."""
    env = {**os.environ, "PYTHONPATH": str(SCRIPTS)}
    p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log.write(p.stdout)
    log.flush()
    return p.returncode, p.stdout


def run_step(name, cmd, retry, log, heal_players=False):
    """heal_players: for the game-log loader. That script exits 0 even when a game fails, so
    scan its output; if a game failed on an unknown player_id (a rookie missing from
    nba_players), add the player via add_player_by_id.py and re-run, up to MAX_HEAL_ROUNDS.
    Any 'game(s) failed' left over counts as a FAIL."""
    attempts = 2 if retry else 1
    start = time.time()
    for attempt in range(1, attempts + 1):
        log.write(f"\n===== {name} (attempt {attempt}) =====\n$ {' '.join(cmd)}\n")
        log.flush()
        code, out = run_once(cmd, log)
        if heal_players and code == 0:
            for round_no in range(1, MAX_HEAL_ROUNDS + 1):
                if "game(s) failed" not in out:
                    break
                ids = sorted(set(FK_MISSING_PLAYER.findall(out)))
                if not ids:
                    code = 1  # games failed for some other reason
                    break
                log.write(f"\n--- healing round {round_no}: adding unknown players {ids} ---\n")
                add_code, _ = run_once([PY, "scripts/ingestion/add_player_by_id.py", *ids], log)
                if add_code != 0:
                    code = 1
                    break
                print(f"  added unknown player id(s) {', '.join(ids)}; reloading game logs...")
                code, out = run_once(cmd, log)
            if code == 0 and "game(s) failed" in out:
                code = 1
        if code == 0:
            return "OK", time.time() - start
        if attempt < attempts:
            print(f"  {name} failed (exit {code}); retrying in {RETRY_PAUSE_SECONDS}s...")
            time.sleep(RETRY_PAUSE_SECONDS)
    return f"FAIL (exit {code})", time.time() - start


def main():
    ap = argparse.ArgumentParser(description="Daily morning routine.")
    ap.add_argument("--date", help="game day to load, YYYY-MM-DD (default: yesterday, America/Chicago)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and run nothing")
    ap.add_argument("--keep-going", action="store_true", help="continue after a failed step")
    ap.add_argument("--backup", action="store_true", help="pg_dump to ~/backups before anything else")
    ap.add_argument("--lite", action="store_true", help="game-night run: game logs (yesterday + today), fouls, sleeper sync, lock inference only")
    args = ap.parse_args()

    now = datetime.now(TZ)
    game_date = args.date or (now.date() - timedelta(days=1)).isoformat()

    if args.dry_run:
        week, run_xw = "<looked up at run time>", "<only if rostered players lack nba_player_id>"
        steps = build_steps(game_date, "<season_id>", None, True)
    else:
        season_id, week = current_week(now.date())
        run_xw = unmatched_rostered_count() > 0
        steps = build_steps(game_date, season_id, week, run_xw)

    if args.lite:
        steps = [x for x in steps if x[0] in LITE_STEPS]
        at = next((k for k, x in enumerate(steps) if x[0] == "game logs"), 0) + 1
        steps.insert(at, ("game logs (today)", [PY, "scripts/ingestion/load_daily_game_logs.py", now.date().isoformat()], True))
    print(f"Daily run: game date {game_date}, fantasy week {week}, crosswalk: {run_xw}{', lite' if args.lite else ''}")
    if args.backup:
        steps.insert(0, ("backup", ["bash", "-c", f"mkdir -p ~/backups && pg_dump postgres > ~/backups/postgres_{now.date()}.sql"], False))

    if args.dry_run:
        for i, (name, cmd, retry) in enumerate(steps, 1):
            print(f"  {i}. {name}: {' '.join(cmd)}{'  [retry once]' if retry else ''}")
        return

    LOG_DIR.mkdir(exist_ok=True)
    import fcntl
    lock = open(LOG_DIR / ".run_daily.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another run_daily is already running; skipping this one.")
        return
    log_path = LOG_DIR / f"daily_{now.date()}.log"
    results = []
    with open(log_path, "a") as log:
        log.write(f"\n######## run started {now.isoformat()} (game date {game_date}) ########\n")
        for name, cmd, retry in steps:
            print(f"-> {name} ...", flush=True)
            status, secs = run_step(name, cmd, retry, log, heal_players=name.startswith("game logs"))
            if name == "game logs (today)" and status.startswith("FAIL"):
                status = "PENDING (retry next run)"   # box score not published yet; the next run and the morning run reload it
            results.append((name, status, secs))
            print(f"   {status} ({secs:.0f}s)")
            if not status.startswith(("OK", "PENDING")) and not args.keep_going:
                print(f"\nStopped at '{name}'. See {log_path}")
                break

    print("\n--- Summary ---")
    for name, status, secs in results:
        print(f"  {status:<14} {name} ({secs:.0f}s)")
    print(f"Full log: {log_path}")
    sys.exit(0 if all(s.startswith(("OK", "PENDING")) for _, s, _ in results) and len(results) == len(steps) else 1)


if __name__ == "__main__":
    main()
