"""
scripts/verify_transactions_independently.py

Standalone re-verification for sleeper_transactions, mirroring
verify_matchup_points_independently.py's approach: deliberately NOT
importing anything from backfill_sleeper_league.py or any other
ingestion script, to rule out a bug in this project's own fetch/parsing
logic by pulling fresh data with completely separate code and diffing
it against what's already stored.

Unlike matchup points (one mutable value per (week, roster_id)),
transactions are keyed by transaction_id and carry several fields that
can each independently disagree -- status, roster_ids, adds, drops --
plus the possibility of a transaction present in one pull but missing
from the other entirely. This checks both: per-field mismatches on
transaction_ids present in both, AND set differences (stored-only /
fresh-only transaction_ids).

sleeper_transactions was never checked for the same live-API
instability found in sleeper_matchups that drove the whole Step 6
investigation -- currently just assumed reliable, never verified
(docs/patch_list.md #6b).

Usage: python scripts/verify_transactions_independently.py <league_id>
"""

import sys
import time
import requests
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from db_connection import get_connection

BASE_URL = "https://api.sleeper.app/v1"
MAX_WEEK = 24
REQUEST_DELAY = 0.3

# Fields checked for per-transaction mismatches. Excludes 'created'
# (a timestamp, not correctness-bearing for this pipeline) and
# 'waiver_budget' (explicitly deferred per patch_list.md's FAAB note --
# never captured during ingestion, out of scope for this check).
COMPARE_FIELDS = ["type", "status", "roster_ids", "adds", "drops"]


def normalize_roster_ids(v):
    """roster_ids is an integer[] column; Sleeper's API and psycopg2
    both return it as a plain list, but nothing guarantees the two
    sides return the SAME ORDER for identical membership. Sort before
    comparing so a harmless order difference doesn't register as a
    false-positive mismatch."""
    return sorted(v) if v else v


def fetch_week(league_id, week):
    resp = requests.get(f"{BASE_URL}/league/{league_id}/transactions/{week}")
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY)
    return resp.json()


def get_stored_transactions(cur, league_id):
    """All stored transactions for this league, keyed by transaction_id."""
    cur.execute("""
        SELECT transaction_id, week, type, status, roster_ids, adds, drops
        FROM sleeper_transactions
        WHERE league_id = %s;
    """, (league_id,))
    stored = {}
    for transaction_id, week, type_, status, roster_ids, adds, drops in cur.fetchall():
        stored[transaction_id] = {
            "week": week, "type": type_, "status": status,
            "roster_ids": normalize_roster_ids(roster_ids), "adds": adds, "drops": drops,
        }
    return stored


def run(league_id):
    conn = get_connection()
    cur = conn.cursor()

    stored = get_stored_transactions(cur, league_id)
    cur.close()
    conn.close()

    print(f"{len(stored)} stored transactions loaded for league_id={league_id}\n")

    fresh = {}
    for week in range(1, MAX_WEEK + 1):
        transactions = fetch_week(league_id, week)
        if not transactions:
            continue
        for t in transactions:
            fresh[t["transaction_id"]] = {
                "week": week, "type": t.get("type"), "status": t.get("status"),
                "roster_ids": normalize_roster_ids(t.get("roster_ids")),
                "adds": t.get("adds"), "drops": t.get("drops"),
            }

    stored_ids = set(stored.keys())
    fresh_ids = set(fresh.keys())

    stored_only = stored_ids - fresh_ids
    fresh_only = fresh_ids - stored_ids
    in_both = stored_ids & fresh_ids

    field_mismatches = []
    for tid in in_both:
        for field in COMPARE_FIELDS:
            if stored[tid][field] != fresh[tid][field]:
                field_mismatches.append((tid, stored[tid]["week"], field, stored[tid][field], fresh[tid][field]))

    print(f"{len(in_both)} transaction_ids present in both.")
    print(f"{len(stored_only)} present in stored only (missing from fresh pull).")
    print(f"{len(fresh_only)} present in fresh only (missing from stored).")
    print(f"{len(field_mismatches)} field-level mismatches on shared transaction_ids.\n")

    if stored_only:
        print("Stored-only transaction_ids (first 20):")
        for tid in sorted(stored_only)[:20]:
            print(f"  {tid}  (week={stored[tid]['week']}, status={stored[tid]['status']})")

    if fresh_only:
        print("\nFresh-only transaction_ids (first 20):")
        for tid in sorted(fresh_only)[:20]:
            print(f"  {tid}  (week={fresh[tid]['week']}, status={fresh[tid]['status']})")

    if field_mismatches:
        print("\ntransaction_id  week  field       stored              fresh")
        for tid, week, field, stored_val, fresh_val in field_mismatches:
            print(f"{tid:<15} {week:>4}  {field:<10}  {stored_val!s:<18}  {fresh_val!s}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python scripts/verify_transactions_independently.py <league_id>")
        sys.exit(1)
    run(sys.argv[1])
