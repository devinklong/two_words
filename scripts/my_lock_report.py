"""
scripts/my_lock_report.py

LOCK / HOLD / PASS read for every player on YOUR roster who played on a given day.
Added 10/22/26. Read-only.

Starts from your roster (roster_ownership) joined to that day's player-games
(game_fantasy_scores_weekly_effective), then attaches the lock signal from
game_lock_signal where one exists. A player who played but has no signal (a rookie, or
under 20 games with no usable baseline) is still listed, marked "no signal", so nobody
is silently dropped.

Columns:
  Score   his fantasy score that game
  Bar     the lock bar for him (score at or above it = LOCK)
  Gap     score minus bar
  Left    games his team still has this fantasy week (effective, after back-to-back adjustment)
  Lock %  the model's percentage_to_lock. It depends only on his tier and games left, NOT on the
          score he just posted, so a LOCK can show a low number; the LOCK/HOLD call comes from Score vs Bar
  Signal  LOCK (score >= bar) / PASS (below bar and no games left) / HOLD (below bar, games left)
  Lock into  for LOCK rows: which lineup slot to lock the score into (added 10/22/26). Rule: use a
          position slot (PG/SG/SF/PF/C) before G/F, and G/F before UTIL. If he fits more than
          one slot in that tier, take the slot with the FEWEST games left among the rest of your
          roster, because it is the hardest to fill later (e.g. a PG/SG when PG-eligible players
          have more games coming than SG-eligible ones -> SG). Games left count every other
          player on your roster (no injury or lineup knowledge) from the day after the report date
          through the end of the fantasy week. The database does not record which slots you
          already locked this week, so pass --filled SLOT,SLOT (e.g. --filled PG,UTIL) to remove them.
Starters ('S') are the players in your lineup now.

Run (from project root):
    python scripts/my_lock_report.py                 (yesterday, roster 8)
    python scripts/my_lock_report.py --date 2026-10-20
    python scripts/my_lock_report.py --filled PG,UTIL   (slots you have already locked this week)
    python scripts/my_lock_report.py --roster-id 8 --season-id 22026
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.append(str(Path(__file__).resolve().parent))

MY_ROSTER_ID = 8  # sweetdiddlydee (same as run_daily.py)

REPORT_SQL = """
SELECT COALESCE(ro.player_name, '(unmatched rookie, sleeper id ' || ro.sleeper_player_id || ')'),
       ro.is_starter,
       ro.sleeper_player_id,
       ro.nba_player_id,
       e.game_id,
       e.fantasy_score,
       e.effective_games_remaining_in_week,
       s.lock_bar,
       s.lock_signal,
       s.percentage_to_lock,
       s.tier,
       s.player_stats_source
FROM roster_ownership ro
JOIN game_fantasy_scores_weekly_effective e
  ON e.player_id = ro.nba_player_id
 AND e.game_date = %(day)s
 AND e.season_id = %(season)s
LEFT JOIN game_lock_signal s
  ON s.game_id = e.game_id AND s.player_id = e.player_id
WHERE ro.roster_id = %(roster)s
  AND ro.league_id = (SELECT league_id FROM sleeper_current_league)
ORDER BY (s.lock_signal = 'LOCK') DESC NULLS LAST, e.fantasy_score DESC
"""

# Every player on the roster: used to count how many games the rest of the roster has left.
ROSTER_SQL = """
SELECT ro.sleeper_player_id, ro.nba_player_id, COALESCE(ro.player_name, 'sleeper id ' || ro.sleeper_player_id)
FROM roster_ownership ro
WHERE ro.roster_id = %(roster)s
  AND ro.league_id = (SELECT league_id FROM sleeper_current_league)
"""

# Roster players with no game on that day, to say who sat out.
DID_NOT_PLAY_SQL = """
SELECT COALESCE(ro.player_name, '(unmatched rookie, sleeper id ' || ro.sleeper_player_id || ')')
FROM roster_ownership ro
WHERE ro.roster_id = %(roster)s
  AND ro.league_id = (SELECT league_id FROM sleeper_current_league)
  AND NOT EXISTS (SELECT 1 FROM game_fantasy_scores_weekly_effective e
                  WHERE e.player_id = ro.nba_player_id AND e.game_date = %(day)s AND e.season_id = %(season)s)
ORDER BY 1
"""


def signal_label(row):
    return row["signal"] if row["signal"] else "no signal"


def format_rows(rows):
    lines = [f"{'':1} {'Player':<24} {'Score':>6} {'Bar':>6} {'Gap':>6} {'Left':>5} {'Lock %':>7}  {'Signal':<10} {'Lock into':<9} Note"]
    lines.append("-" * 104)
    for r in rows:
        bar = f"{r['bar']:.1f}" if r["bar"] is not None else "-"
        gap = f"{r['score'] - r['bar']:+.1f}" if r["bar"] is not None else "-"
        pct = f"{r['lock_pct']*100:.0f}%" if r["lock_pct"] is not None else "-"
        note = ""
        if not r["signal"]:
            note = "no baseline yet (rookie / under 20 games)"
        elif r["source"] and r["source"] != "season":
            note = f"bar from {r['source']} stats"
        lines.append(f"{'S' if r['starter'] else ' ':1} {r['name']:<24} {r['score']:>6.1f} {bar:>6} {gap:>6} "
                     f"{r['left']:>5.1f} {pct:>7}  {signal_label(r):<10} {r.get('slot') or '':<9} {note}")
    return lines


def recommend_slots(conn, rows, args, day):
    """Fills r['slot'] for LOCK rows; returns (ctx_ok, notes, supply)."""
    from waiver_bid_advisor import fetch_schedule_context, fetch_player_teams, fetch_taxi, POSITIONS_SQL
    from lineup_fit import slot_supply, recommend_lock_slots

    cur0 = conn.cursor()
    cur0.execute("SELECT league_id FROM sleeper_current_league")
    taxi = fetch_taxi(conn, cur0.fetchone()[0]).get(args.roster_id, set())
    cur0.close()
    lockers = [r for r in rows if r["signal"] == "LOCK" and r["sid"] not in taxi]
    if not lockers:
        return True, [], {}
    ctx = fetch_schedule_context(conn, args.season_id, day, None)
    if ctx is None:
        return False, ["Slot advice skipped: that date is not inside a fantasy week."], {}
    cur = conn.cursor()
    cur.execute(POSITIONS_SQL)
    positions = {sid: list(ps) for sid, ps in cur.fetchall()}
    cur.execute(ROSTER_SQL, {"roster": args.roster_id})
    roster = cur.fetchall()
    cur.close()
    teams = fetch_player_teams(conn, [nba for _sid, nba, _nm in roster])
    locker_ids = {r["nba_id"] for r in lockers}
    candidates = []
    for sid, nba, _nm in roster:
        if nba in locker_ids or sid in taxi:
            continue
        team_id = teams.get(nba, (None, None))[0]
        dates = ctx["games"].get(team_id, set()) if team_id else set()
        left = sum(1 for d in dates if day < d <= ctx["end"])
        candidates.append((positions.get(sid, []), left))
    supply = slot_supply(candidates)
    filled = [x.strip().upper() for x in args.filled.split(",") if x.strip()] if args.filled else []
    rec = recommend_lock_slots(
        [{"name": r["name"], "positions": positions.get(r["sid"], []), "gap": r["score"] - (r["bar"] or 0)} for r in lockers],
        supply, filled)
    notes = []
    for r in lockers:
        slot, why = rec[r["name"]]
        r["slot"] = slot or "?"
        pos = "/".join(positions.get(r["sid"], [])) or "no position on file"
        notes.append(f"{r['name']} ({pos}): {slot or 'no slot'} - {why}")
    taxi_names = [nm for sid, _nba, nm in roster if sid in taxi]
    if taxi_names:
        notes.append("On taxi, not lockable: " + ", ".join(taxi_names))
    return True, notes, supply


def main():
    ap = argparse.ArgumentParser(description="Lock/hold read for your roster's players on a given day.")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday, America/Chicago)")
    ap.add_argument("--roster-id", type=int, default=MY_ROSTER_ID)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--filled", help="slots already locked this week, comma separated, e.g. PG,UTIL")
    args = ap.parse_args()

    day = args.date or (datetime.now(ZoneInfo("America/Chicago")).date() - timedelta(days=1)).isoformat()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    params = {"day": day, "season": args.season_id, "roster": args.roster_id}
    cur.execute(REPORT_SQL, params)
    rows = [{"name": r[0], "starter": r[1], "sid": r[2], "nba_id": r[3], "game_id": r[4], "score": float(r[5]),
             "left": float(r[6] or 0), "bar": float(r[7]) if r[7] is not None else None, "signal": r[8],
             "lock_pct": float(r[9]) if r[9] is not None else None, "tier": r[10], "source": r[11]}
            for r in cur.fetchall()]
    cur.execute(DID_NOT_PLAY_SQL, params)
    sat = [r[0] for r in cur.fetchall()]
    cur.close()
    notes, supply = [], {}
    if rows:
        _ok, notes, supply = recommend_slots(conn, rows, args, date.fromisoformat(day))
    conn.close()

    print(f"\nLock report for roster {args.roster_id}, games on {day} ({len(rows)} played)")
    if not rows:
        print("No roster players have a game on that date. Has the daily load run for it?")
        return
    print("\n".join(format_rows(rows)))
    counts = {k: sum(1 for r in rows if signal_label(r) == k) for k in ("LOCK", "HOLD", "PASS", "no signal")}
    if notes:
        print("\nWhere to lock:")
        for n in notes:
            print("  " + n)
        if supply:
            order = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
            print("  Games left for the rest of your roster, by slot: " + ", ".join(f"{k} {supply[k]:g}" for k in order if k in supply))
    print(f"\nLOCK {counts['LOCK']}, HOLD {counts['HOLD']}, PASS {counts['PASS']}, no signal {counts['no signal']}")
    if sat:
        print(f"Did not play ({len(sat)}): {', '.join(sat)}")
    print("LOCK = score at or above his lock bar. PASS = below it with no games left. HOLD = below it, games left.")
    print("Lock into: position slots before G/F before UTIL; among eligible ones, the slot with the fewest games left for the rest of your roster.")
    print("Assumes every slot is open unless you pass --filled; counts every rostered player except taxi (no injury knowledge).")
    print("Lock % comes from his tier and games left only (not tonight's score); the signal comes from Score vs Bar.")


if __name__ == "__main__":
    main()
