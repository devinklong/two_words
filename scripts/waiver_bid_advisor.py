"""
scripts/waiver_bid_advisor.py

Adds a price and competition read to the waiver-wire finder: for each free
agent the finder ranks, what players of that tier have cost in this league,
which rivals can afford a serious bid, and a suggested FAAB range.
Added 10/21/26. Read-only (no writes). Does not change waiver_wire_finder.py;
it imports build_report() and uses its rows as they are.

Inputs (all already in the database):
  * waiver_wire_finder.build_report()     the ranked free agents (tier, spike bar, durability)
  * sleeper_transactions                  completed waiver claims + waiver_bid, 2024 onward
  * sleeper_player_crosswalk / nba_player_tiers_in_use   to put each past claim in a tier
  * sleeper_faab_balance                  each roster's remaining FAAB right now

How the suggestion is made (a transparent heuristic, not a model):
  1. PRICE: for the player's tier, the winning bids of past completed claims
     (median, 75th and 90th percentile, max, count). Players with no tier that
     season, or not in the crosswalk, count as 'untiered'.
  2. COMPETITION: a rival is a "big bidder" if at least --min-big-share of their
     winning claims (any season on record) were bids of --big-bid dollars or more,
     and they have at least --min-big-wins such claims. Defaults: $10, 20%, 3 wins.
     (Checked 10/21/26 against this league: Yaak0v 40%, cocohebbles 29%, Pete1771 29%;
     everyone else under 13%. A bare "ever bid $10+" rule flagged all nine rivals.)
     The player is "contested" if a big bidder still has at least --big-bid dollars left.
  3. RANGE: uncontested -> median to 75th percentile (at least $1);
            contested   -> 75th to 90th percentile.
     Capped at your remaining budget, and at (richest rival's remaining + 1),
     because nobody can bid more than they have left.
  A tier with fewer than --min-sample past claims is marked 'thin': read it as a hint.
Limits: three seasons of bids; offseason/preseason bidding differs from in-season;
rival positional need and roster limits are NOT considered yet; ties on priority
are not modeled.

Run (from project root):
    python scripts/waiver_bid_advisor.py --season-id 22026 --limit 10
    python scripts/waiver_bid_advisor.py --season-id 22026 --player drummond
"""

import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

MY_ROSTER_ID = 8  # sweetdiddlydee (same as run_daily.py)

# Tier of every past completed waiver claim, in the tiers of the season the claim was made.
CLASS_STATS_SQL = """
WITH claims AS (
    SELECT t.waiver_bid,
           k.sleeper_player_id,
           '2' || l.season AS season_id
    FROM sleeper_transactions t
    JOIN sleeper_leagues l ON l.league_id = t.league_id
    CROSS JOIN LATERAL jsonb_object_keys(
        CASE WHEN jsonb_typeof(t.adds) = 'object' THEN t.adds ELSE '{}'::jsonb END
    ) AS k(sleeper_player_id)
    WHERE t.type = 'waiver' AND t.status = 'complete' AND t.waiver_bid IS NOT NULL
),
classified AS (
    SELECT c.waiver_bid,
           COALESCE(pt.tier, 'untiered') AS tier
    FROM claims c
    LEFT JOIN sleeper_player_crosswalk x ON x.sleeper_player_id::text = c.sleeper_player_id
    LEFT JOIN nba_player_tiers_in_use pt
           ON pt.player_id = x.nba_player_id AND pt.season_id::text = c.season_id
)
SELECT tier,
       COUNT(*) AS n,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY waiver_bid) AS median,
       percentile_cont(0.75) WITHIN GROUP (ORDER BY waiver_bid) AS p75,
       percentile_cont(0.9)  WITHIN GROUP (ORDER BY waiver_bid) AS p90,
       MAX(waiver_bid) AS max_bid
FROM classified
GROUP BY tier
"""

# Each roster in the current league: remaining FAAB now, and its history of big winning bids.
RIVALS_SQL = """
WITH hist AS (
    SELECT u.display_name AS owner_name,
           COUNT(*) AS wins,
           COUNT(*) FILTER (WHERE t.waiver_bid >= %(big)s) AS big_wins,
           COALESCE(MAX(t.waiver_bid), 0) AS max_win
    FROM sleeper_transactions t
    JOIN sleeper_rosters r ON r.league_id = t.league_id AND r.roster_id = t.roster_ids[1]
    JOIN sleeper_users u ON u.league_id = r.league_id AND u.user_id = r.owner_id
    WHERE t.type = 'waiver' AND t.status = 'complete' AND t.waiver_bid IS NOT NULL
    GROUP BY u.display_name
)
SELECT f.roster_id, f.owner_name, f.remaining,
       COALESCE(h.wins, 0), COALESCE(h.big_wins, 0), COALESCE(h.max_win, 0)
FROM sleeper_faab_balance f
LEFT JOIN hist h ON h.owner_name = f.owner_name
WHERE f.league_id = %(league)s
ORDER BY f.roster_id
"""


def fetch_class_stats(conn):
    cur = conn.cursor()
    cur.execute(CLASS_STATS_SQL)
    stats = {r[0]: {"n": r[1], "median": float(r[2]), "p75": float(r[3]), "p90": float(r[4]), "max": r[5]}
             for r in cur.fetchall()}
    cur.close()
    return stats


def fetch_rivals(conn, league_id, big_bid):
    cur = conn.cursor()
    cur.execute(RIVALS_SQL, {"big": big_bid, "league": league_id})
    rows = [{"roster_id": r[0], "owner": r[1], "remaining": r[2], "wins": r[3], "big_wins": r[4], "max_win": r[5]}
            for r in cur.fetchall()]
    cur.close()
    return rows


def is_big_bidder(r, min_big_wins, min_big_share):
    """True when enough of a rival's winning claims were big bids (a rate, not 'ever')."""
    return r["wins"] > 0 and r["big_wins"] >= min_big_wins and r["big_wins"] / r["wins"] >= min_big_share


def suggest(stats, rivals, my_roster_id, big_bid, min_big_wins, min_big_share, min_sample):
    """Pure function: returns a dict describing the read for one tier's stats (or None) given rivals."""
    me = next((r for r in rivals if r["roster_id"] == my_roster_id), None)
    others = [r for r in rivals if r["roster_id"] != my_roster_id]
    my_remaining = me["remaining"] if me else None
    known = [r["remaining"] for r in others if r["remaining"] is not None]
    richest = max(known) if known else None

    big_bidders = [r["owner"] for r in others
                   if is_big_bidder(r, min_big_wins, min_big_share)
                   and r["remaining"] is not None and r["remaining"] >= big_bid]
    contested = bool(big_bidders)

    if stats is None:
        return {"low": None, "high": None, "contested": contested, "big_bidders": big_bidders,
                "thin": True, "capped_by": None, "richest": richest, "my_remaining": my_remaining}

    if contested:
        low, high = stats["p75"], stats["p90"]
    else:
        low, high = stats["median"], stats["p75"]
    low = max(1, round(low))
    high = max(low, round(high))

    capped_by = None
    if richest is not None and high > richest + 1:
        high, capped_by = richest + 1, "richest rival"
    if my_remaining is not None and high > my_remaining:
        high, capped_by = my_remaining, "your budget"
    low = min(low, high)

    return {"low": low, "high": high, "contested": contested, "big_bidders": big_bidders,
            "thin": stats["n"] < min_sample, "capped_by": capped_by, "richest": richest,
            "my_remaining": my_remaining}


def print_advice(rows, class_stats, args):
    print(f"\nWaiver bid advisor ({len(rows)} free agents shown). Your budget left: "
          f"{rows[0][2]['my_remaining'] if rows and rows[0][2]['my_remaining'] is not None else 'unknown'}"
          f"; richest rival: {rows[0][2]['richest'] if rows and rows[0][2]['richest'] is not None else 'unknown'}")
    print(f"{'Player':<24} {'Tier':<8} {'SpikeBar':>8} {'Past n':>6} {'Med':>5} {'P90':>5} {'Max':>5}  {'Suggest':>9}  Competition")
    print("-" * 110)
    for r, st, s in rows:
        if st is None:
            price = f"{'-':>6} {'-':>5} {'-':>5} {'-':>5}"
            sug = "no history"
        else:
            price = f"{st['n']:>6} {st['median']:>5.0f} {st['p90']:>5.0f} {st['max']:>5}"
            sug = f"${s['low']}-${s['high']}"
            if s["thin"]:
                sug += " (thin)"
        comp = ("contested: " + ", ".join(s["big_bidders"])) if s["contested"] else "uncontested"
        if s["capped_by"]:
            comp += f"  [capped by {s['capped_by']}]"
        print(f"{r['player_name']:<24} {r['tier']:<8} {r['spike_bar']:>8.2f} {price}  {sug:>9}  {comp}")
    print(f"\nContested = a big bidder (>= {args.min_big_share*100:.0f}% of their wins at ${args.big_bid}+, at least "
          f"{args.min_big_wins} such wins) who still has ${args.big_bid}+ left.")
    print("Suggest: uncontested = median..75th pct of past winning bids for that tier; contested = 75th..90th pct;")
    print("capped at your budget and at (richest rival's remaining + 1). 'thin' = fewer than "
          f"{args.min_sample} past claims in that tier. Heuristic, not a forecast.")


def print_rivals(rivals, args):
    print("\nRivals (FAAB left, history of winning bids):")
    print(f"{'Owner':<18} {'Left':>5} {'Wins':>5} {'Big':>4} {'Big %':>6}  Big bidder?")
    for r in sorted(rivals, key=lambda x: -(x["big_wins"] / x["wins"] if x["wins"] else 0)):
        if r["roster_id"] == args.my_roster_id:
            continue
        pct = 100.0 * r["big_wins"] / r["wins"] if r["wins"] else 0.0
        left = "?" if r["remaining"] is None else r["remaining"]
        flag = "yes" if is_big_bidder(r, args.min_big_wins, args.min_big_share) else ""
        print(f"{r['owner']:<18} {left!s:>5} {r['wins']:>5} {r['big_wins']:>4} {pct:>5.1f}%  {flag}")


def main():
    ap = argparse.ArgumentParser(description="FAAB bid and competition read for the waiver-wire finder's targets.")
    ap.add_argument("--season-id", required=True, help="e.g. 22026")
    ap.add_argument("--limit", type=int, default=10, help="top N free agents from the finder (default 10)")
    ap.add_argument("--player", help="only free agents whose name contains this text (case-insensitive)")
    ap.add_argument("--my-roster-id", type=int, default=MY_ROSTER_ID)
    ap.add_argument("--big-bid", type=int, default=10, help="what counts as a big winning bid, dollars (default 10)")
    ap.add_argument("--min-big-wins", type=int, default=3, help="big wins needed to call a rival a big bidder (default 3)")
    ap.add_argument("--min-big-share", type=float, default=0.20, help="share of a rival's wins that must be big bids (default 0.20)")
    ap.add_argument("--min-sample", type=int, default=5, help="past claims in a tier before it is not 'thin' (default 5)")
    args = ap.parse_args()

    from db_connection import get_connection
    from waiver_wire_finder import get_current_league_id, build_report

    conn = get_connection()
    try:
        league_id = get_current_league_id(conn)
        report = build_report(conn, league_id, args.season_id)
        if args.player:
            report = [r for r in report if args.player.lower() in r["player_name"].lower()]
        report = report[: args.limit]
        if not report:
            print("No matching free agents in the finder's report.")
            return
        class_stats = fetch_class_stats(conn)
        rivals = fetch_rivals(conn, league_id, args.big_bid)
    finally:
        conn.close()

    rows = [(r, class_stats.get(r["tier"]),
             suggest(class_stats.get(r["tier"]), rivals, args.my_roster_id, args.big_bid, args.min_big_wins, args.min_big_share, args.min_sample))
            for r in report]
    print_advice(rows, class_stats, args)
    print_rivals(rivals, args)


if __name__ == "__main__":
    main()
