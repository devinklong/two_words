"""
scripts/verify_current_league_independently.py

Independent re-verification of what sleeper_daily_sync stored for the
CURRENT league (resolved via sleeper_current_league), for one week.
Mirrors verify_transactions_independently.py / verify_matchup_points_
independently.py in spirit, but those are hardcoded to the 2024/2025
leagues -- this one follows the live league.

Deliberately does NOT import anything from backfill_sleeper_league.py or
the sync scripts: it pulls fresh data from Sleeper's public API with its
own urllib code and diffs it against the database, so a bug in the
project's own fetch/parse logic can't hide itself. Only the DB read uses
db_connection. READ-ONLY: the session is set read-only and nothing is
written.

Checks, for league L and week W:
  1. ROSTERS: per roster_id, players (as a set) and starters (ordered).
  2. MATCHUPS (week W): per roster_id, matchup_id, players (set),
     starters (ordered -- slot order matters for slot alignment).
  3. TRANSACTIONS (round W): transaction_ids stored-only / fresh-only,
     and per-field mismatches on ids in both (type, status, roster_ids,
     adds, drops).

NOTE on transactions: only rows whose stored `week` equals W are compared
against Sleeper's round W, since that is what the sync pulls for the
current week. Earlier-week/offseason rows are not re-fetched here.

ASSUMPTIONS (not confirmed against the live schema): sleeper_rosters has
players/starters columns; sleeper_matchups has matchup_id/players/
starters; sleeper_transactions has transaction_id/type/status/week/
roster_ids/adds/drops. A wrong column gives a clear SKIP, not a crash.

Run (from project root):
    python scripts/verify_current_league_independently.py --week 1
"""

import argparse
import json
import sys
import urllib.request
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

BASE = "https://api.sleeper.app/v1"
TALLY = {"OK": 0, "FAIL": 0, "SKIP": 0}


def report(status, label, detail=""):
    TALLY[status] += 1
    print(f"[{status:<4}] {label}" + (f" -- {detail}" if detail else ""))


def fetch(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "two_words-verify/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def q(conn, sql, params=()):
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        return cur.fetchall(), None
    except Exception as e:  # noqa: BLE001 -- SKIP instead of crash
        conn.rollback()
        return None, str(e).strip().splitlines()[0]
    finally:
        cur.close()


def norm_list(x):
    return [str(i) for i in (x or [])]


def check_rosters(conn, league_id):
    print("\n=== Rosters ===")
    rows, err = q(conn, "SELECT roster_id, players, starters FROM sleeper_rosters WHERE league_id = %s", (league_id,))
    if err:
        return report("SKIP", "stored rosters", err)
    stored = {r[0]: (set(norm_list(r[1])), norm_list(r[2])) for r in rows}
    fresh = {r["roster_id"]: (set(norm_list(r.get("players"))), norm_list(r.get("starters")))
             for r in fetch(f"/league/{league_id}/rosters")}
    bad = []
    for rid in sorted(set(stored) | set(fresh)):
        if rid not in stored or rid not in fresh:
            bad.append((rid, "present in only one side"))
        else:
            if stored[rid][0] != fresh[rid][0]:
                d = stored[rid][0] ^ fresh[rid][0]
                bad.append((rid, f"players differ: {sorted(d)[:6]}"))
            if stored[rid][1] != fresh[rid][1]:
                bad.append((rid, "starters differ"))
    report("OK" if not bad else "FAIL", f"{len(fresh)} rosters vs stored", "identical" if not bad else str(bad[:6]))


def check_matchups(conn, league_id, week):
    print(f"\n=== Matchups (week {week}) ===")
    rows, err = q(conn, "SELECT roster_id, matchup_id, players, starters FROM sleeper_matchups WHERE league_id = %s AND week = %s", (league_id, week))
    if err:
        return report("SKIP", "stored matchups", err)
    stored = {r[0]: (r[1], set(norm_list(r[2])), norm_list(r[3])) for r in rows}
    fresh = {m["roster_id"]: (m.get("matchup_id"), set(norm_list(m.get("players"))), norm_list(m.get("starters")))
             for m in fetch(f"/league/{league_id}/matchups/{week}")}
    bad = []
    for rid in sorted(set(stored) | set(fresh)):
        if rid not in stored or rid not in fresh:
            bad.append((rid, "present in only one side"))
            continue
        if stored[rid][0] != fresh[rid][0]:
            bad.append((rid, f"matchup_id {stored[rid][0]} vs {fresh[rid][0]}"))
        if stored[rid][1] != fresh[rid][1]:
            bad.append((rid, "players differ"))
        if stored[rid][2] != fresh[rid][2]:
            bad.append((rid, "starters differ (order matters)"))
    report("OK" if not bad else "FAIL", f"{len(fresh)} matchup rows vs stored", "identical" if not bad else str(bad[:6]))


def norm_map(m):
    return {str(k): int(v) for k, v in (m or {}).items()}


def check_transactions(conn, league_id, week):
    print(f"\n=== Transactions (round {week}) ===")
    rows, err = q(conn, "SELECT transaction_id, type, status, roster_ids, adds, drops FROM sleeper_transactions WHERE league_id = %s AND week = %s", (league_id, week))
    if err:
        return report("SKIP", "stored transactions", err)
    stored = {str(r[0]): (r[1], r[2], set(int(i) for i in (r[3] or [])), norm_map(r[4]), norm_map(r[5])) for r in rows}
    fresh = {}
    for t in fetch(f"/league/{league_id}/transactions/{week}"):
        fresh[str(t["transaction_id"])] = (t.get("type"), t.get("status"), set(int(i) for i in (t.get("roster_ids") or [])),
                                           norm_map(t.get("adds")), norm_map(t.get("drops")))
    only_stored = sorted(set(stored) - set(fresh))
    only_fresh = sorted(set(fresh) - set(stored))
    report("OK" if not only_stored else "FAIL", "stored-only transaction_ids", str(only_stored[:6]) if only_stored else "none")
    report("OK" if not only_fresh else "FAIL", "fresh-only transaction_ids (missed by sync)", str(only_fresh[:6]) if only_fresh else "none")
    names = ["type", "status", "roster_ids", "adds", "drops"]
    mism = []
    for tid in sorted(set(stored) & set(fresh)):
        for i, n in enumerate(names):
            if stored[tid][i] != fresh[tid][i]:
                mism.append((tid, n))
    report("OK" if not mism else "FAIL", f"{len(set(stored) & set(fresh))} shared ids, field comparison", "identical" if not mism else str(mism[:6]))


def main():
    p = argparse.ArgumentParser(description="Independent diff of stored vs live Sleeper data for the current league.")
    p.add_argument("--week", type=int, default=1)
    args = p.parse_args()
    conn = get_connection()
    conn.set_session(readonly=True)
    rows, err = q(conn, "SELECT league_id FROM sleeper_current_league")
    if err or not rows:
        sys.exit(f"cannot resolve current league: {err or 'no rows'}")
    league_id = rows[0][0]
    print(f"Verifying league {league_id}, week {args.week} against the live Sleeper API...")
    for fn, a in ((check_rosters, ()), (check_matchups, (args.week,)), (check_transactions, (args.week,))):
        try:
            fn(conn, league_id, *a)
        except Exception as e:  # noqa: BLE001
            report("FAIL", fn.__name__, f"{type(e).__name__}: {e}")
    conn.close()
    print("\n--- Summary ---\n" + "  ".join(f"{k}={v}" for k, v in TALLY.items()))
    sys.exit(1 if TALLY["FAIL"] else 0)


if __name__ == "__main__":
    main()
