"""
scripts/sleeper/backfill_waiver_bids.py

One-off: fills sleeper_transactions.waiver_bid for transactions already in the
database, across every season in the league chain. Added 10/21/26.

It touches ONLY the waiver_bid column (UPDATE ... WHERE transaction_id = ...), never
inserts or changes any other column, so it cannot disturb the rosters/matchups/
transactions data or the known 2025-26 week 1-18 instability. Transactions Sleeper
no longer returns are simply left NULL. Idempotent.

Run (from project root), after schema/tables/sleeper_transactions_waiver_bid.sql:
    python scripts/sleeper/backfill_waiver_bids.py
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
sys.path.append(str(Path(__file__).resolve().parent))

from backfill_sleeper_league import fetch, get_season_chain, CURRENT_LEAGUE_ID, MAX_WEEK
from db_connection import get_connection


def waiver_bid(t):
    """settings.waiver_bid as an int, or None when the transaction has no bid."""
    bid = (t.get("settings") or {}).get("waiver_bid")
    return int(bid) if bid is not None else None


def main():
    conn = get_connection()
    cur = conn.cursor()
    chain = get_season_chain(CURRENT_LEAGUE_ID)
    seen = updated = with_bid = 0
    for league in chain:
        league_id = league["league_id"]
        for round_ in range(1, MAX_WEEK + 1):
            for t in fetch(f"/league/{league_id}/transactions/{round_}") or []:
                seen += 1
                bid = waiver_bid(t)
                cur.execute("UPDATE sleeper_transactions SET waiver_bid = %s WHERE transaction_id = %s",
                            (bid, t["transaction_id"]))
                updated += cur.rowcount
                with_bid += bid is not None
        conn.commit()
        print(f"  season {league.get('season')}: done")
    cur.close()
    conn.close()
    print(f"{seen} transactions seen, {updated} matched a stored row, {with_bid} carry a waiver bid.")


if __name__ == "__main__":
    main()
