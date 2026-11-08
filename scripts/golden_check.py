"""Golden checks: freeze what the database says about COMPLETED data, then prove a refactor did not change it. Read-only.

Record once, before refactoring, from a database you trust:   python scripts/golden_check.py --record
After any refactor or rebuild (rebuild_lock_pipeline.py, a moved script, a changed view):   python scripts/golden_check.py
Exit code 1 on any difference. Each difference prints the first rows that moved, so a real change (a late NBA correction) can be told apart from a break.

What is frozen (tests/golden/expected.json): only data that cannot change by itself.
  completed seasons 22021-22025: fantasy score totals, season stats, lock signal counts and bar sums, the lock win-rate view
  this season, weeks 1 and 2 only: every stored lock (180 rows), week totals and statuses, reconciliation per team, game score totals
Week 3 onward is live and is never frozen. Also checked every time, against fixed expected answers:
  invariants   lock_bar(40,10,35,0.5) = 45 and lock_bar(20,5,35,0.5) = 35; weeks 1 and 2 reconcile 10 of 10; the week 2 app-screenshot CSV
               (data/app_screenshots/week2_slots.csv) equals the stored locks slot for slot
  unit tests   python tests/test_lineup_fit.py (answers worked out by hand) exits 0

Re-record only on purpose (--record --force) after a change you intended, and say why in the commit message.
"""
import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(Path(__file__).resolve().parent))
EXPECTED = ROOT / "tests" / "golden" / "expected.json"
FULL_ROWS_UP_TO = 400   # results this small are stored row by row so a diff can show what moved

LEAGUE = "(SELECT league_id FROM sleeper_current_league)"
QUERIES = {
    "season_game_scores": """SELECT season_id, count(*), round(sum(fantasy_score)::numeric, 2) FROM game_fantasy_scores_weekly_effective
                             WHERE season_id < '22026' GROUP BY 1 ORDER BY 1""",
    "current_season_weeks_1_2_scores": """SELECT week_number, count(*), round(sum(fantasy_score)::numeric, 2) FROM game_fantasy_scores_weekly_effective
                             WHERE season_id = '22026' AND week_number IN (1, 2) GROUP BY 1 ORDER BY 1""",
    "season_stats": """SELECT season_id, count(*), sum(games_played), round(avg(avg_fantasy_score)::numeric, 4), round(avg(stddev_fantasy_score)::numeric, 4)
                       FROM player_season_fantasy_stats WHERE season_id < '22026' GROUP BY 1 ORDER BY 1""",
    "lock_signal_counts": """SELECT season_id, player_stats_source, lock_signal, count(*) FROM game_lock_signal
                             WHERE season_id < '22026' GROUP BY 1, 2, 3 ORDER BY 1, 2, 3""",
    "lock_bar_sums": """SELECT season_id, count(*), round(sum(lock_bar)::numeric, 2), round(sum(percentage_to_lock)::numeric, 2) FROM game_lock_signal
                        WHERE season_id < '22026' GROUP BY 1 ORDER BY 1""",
    "lock_win_rate": "SELECT tier, games_left, above_bar, n, lock_was_right FROM nba_lock_win_rate ORDER BY 1, 2, 3",
    "weeks_1_2_locks": f"""SELECT week_number, roster_id, slot, player_name, game_id, round(fantasy_score::numeric, 2) FROM nba_lock_decisions
                           WHERE league_id = {LEAGUE} AND week_number IN (1, 2) ORDER BY 1, 2, 3""",
    "weeks_1_2_totals": f"""SELECT week_number, roster_id, round(final_points::numeric, 2), status, round(explained_diff::numeric, 2) FROM sleeper_roster_week_totals
                            WHERE league_id = {LEAGUE} AND week_number IN (1, 2) ORDER BY 1, 2""",
    "weeks_1_2_reconciliation": f"""SELECT week_number, roster_id, reconciled, checksum_status FROM nba_week_reconciliation
                                    WHERE league_id = {LEAGUE} AND week_number IN (1, 2) ORDER BY 1, 2""",
}


def canon(v):
    if isinstance(v, Decimal):
        return round(float(v), 4)
    if isinstance(v, float):
        return round(v, 4)
    if isinstance(v, (date, datetime)):
        return str(v)
    return v


def run_queries(cur):
    out = {}
    for name, sql in QUERIES.items():
        try:
            cur.execute(sql)
            rows = [[canon(c) for c in r] for r in cur.fetchall()]
        except Exception as e:
            cur.connection.rollback()
            cur.execute("SET TRANSACTION READ ONLY")
            out[name] = {"error": f"{type(e).__name__}: {str(e).splitlines()[0]}"}
            continue
        entry = {"rows": len(rows), "digest": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]}
        if len(rows) <= FULL_ROWS_UP_TO:
            entry["data"] = rows
        out[name] = entry
    return out


def invariants(cur):
    """[(name, ok, message)] against fixed answers."""
    res = []
    cur.execute("SELECT lock_bar(40, 10, 35, 0.5), lock_bar(20, 5, 35, 0.5)")
    a, b = (float(x) for x in cur.fetchone())
    res.append(("lock_bar function", abs(a - 45) < 1e-6 and abs(b - 35) < 1e-6, f"lock_bar(40,10)={a}, lock_bar(20,5)={b} (expected 45 and 35)"))
    cur.execute(f"""SELECT week_number, count(*), count(*) FILTER (WHERE reconciled) FROM nba_week_reconciliation
                    WHERE league_id = {LEAGUE} AND week_number IN (1, 2) GROUP BY 1 ORDER BY 1""")
    got = cur.fetchall()
    res.append(("weeks 1-2 reconcile", got == [(1, 10, 10), (2, 10, 10)], f"{got} (expected [(1, 10, 10), (2, 10, 10)])"))
    path = ROOT / "data" / "app_screenshots" / "week2_slots.csv"
    if path.exists():
        cur.execute(f"SELECT roster_id, slot, round(fantasy_score::numeric, 2) FROM nba_lock_decisions WHERE league_id = {LEAGUE} AND week_number = 2")
        db = {(r, s): float(f) for r, s, f in cur.fetchall()}
        bad = [f"roster {r['roster_id']} {r['slot']}: app {r['score']} vs db {db.get((int(r['roster_id']), r['slot']))}"
               for r in csv.DictReader(open(path))
               if r["player"] != "EMPTY" and abs(db.get((int(r["roster_id"]), r["slot"]), -999) - float(r["score"])) > 0.011]
        res.append(("week 2 vs app screenshots", not bad, "all slots match" if not bad else "; ".join(bad[:5])))
    else:
        res.append(("week 2 vs app screenshots", False, f"{path} is missing"))
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", action="store_true", help="write tests/golden/expected.json from the database as it is now")
    ap.add_argument("--force", action="store_true", help="allow --record to overwrite an existing file")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    got = run_queries(cur)
    errors = {k: v["error"] for k, v in got.items() if "error" in v}
    inv = invariants(cur)
    cur.close(); conn.close()

    if args.record:
        if errors:
            raise SystemExit("cannot record; these queries failed: " + "; ".join(f"{k}: {v}" for k, v in errors.items()))
        if EXPECTED.exists() and not args.force:
            raise SystemExit(f"{EXPECTED} exists; use --record --force only after a change you meant to make")
        EXPECTED.parent.mkdir(parents=True, exist_ok=True)
        EXPECTED.write_text(json.dumps({"recorded": datetime.now().isoformat(timespec="seconds"), "queries": got}, indent=1, sort_keys=True))
        print(f"recorded {len(got)} frozen results to {EXPECTED}")
        for name, entry in got.items():
            print(f"  {name:<34} {entry['rows']:>5} rows")
        bad_inv = [n for n, ok, _m in inv if not ok]
        if bad_inv:
            print("WARNING: recorded, but these invariants are not true right now: " + ", ".join(bad_inv))
        return

    if not EXPECTED.exists():
        raise SystemExit(f"{EXPECTED} not found; run --record first (on a database you trust)")
    exp = json.loads(EXPECTED.read_text())["queries"]
    failures = 0
    print(f"golden checks against the results recorded {json.loads(EXPECTED.read_text())['recorded']}\n")
    for name in QUERIES:
        e, g = exp.get(name), got[name]
        if "error" in g:
            print(f"FAIL  {name}: query failed ({g['error']})")
            failures += 1
        elif e is None:
            print(f"FAIL  {name}: not in the recorded file; re-record on purpose")
            failures += 1
        elif e["digest"] == g["digest"]:
            print(f"PASS  {name:<34} {g['rows']} rows identical")
        else:
            failures += 1
            print(f"FAIL  {name}: {e['rows']} rows recorded, {g['rows']} now")
            if "data" in e and "data" in g:
                old, new = {json.dumps(r) for r in e["data"]}, {json.dumps(r) for r in g["data"]}
                for r in sorted(old - new)[:5]:
                    print(f"        was: {r}")
                for r in sorted(new - old)[:5]:
                    print(f"        now: {r}")
    for name, ok, msg in inv:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<34} {msg if not ok else ''}")
        failures += 0 if ok else 1
    try:
        r = subprocess.run([sys.executable, str(ROOT / "tests" / "test_lineup_fit.py")], capture_output=True, text=True, timeout=120)
        ok = r.returncode == 0
        print(f"{'PASS' if ok else 'FAIL'}  unit tests (lineup_fit)            {'' if ok else (r.stdout + r.stderr).strip().splitlines()[-3:]}")
        failures += 0 if ok else 1
    except Exception as e:
        print(f"FAIL  unit tests (lineup_fit): {e}")
        failures += 1
    print(f"\n{'ALL GOLDEN CHECKS PASS' if not failures else str(failures) + ' GOLDEN CHECK(S) FAILED'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
