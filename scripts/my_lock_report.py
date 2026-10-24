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
  Lock right  the chance that locking this score now turns out to be the right call (changed 10/23/26):
          LOCK rows: from history, how often a score at/above the bar in the same tier, with the same
          games left and about this far above the bar, was NOT beaten by a later game that week
          (view nba_lock_win_rate; the Note shows how many past cases it is based on).
          HOLD / PASS rows: the model's percentage_to_lock (a fitted curve on tier and games left, built
          only from below-bar scores, which is why it is NOT used for LOCK rows). 0 games left = 100%.
  Signal  LOCK (score >= bar) / PASS (below bar and no games left) / HOLD (below bar, games left)
  Lock into  for LOCK rows: which lineup slot to lock the score into (added 10/22/26). Rule: use a
          position slot (PG/SG/SF/PF/C) before G/F, and G/F before UTIL. If he fits more than
          one slot in that tier, take the slot with the FEWEST games left among the rest of your
          roster, because it is the hardest to fill later (e.g. a PG/SG when PG-eligible players
          have more games coming than SG-eligible ones -> SG). Games left count every other
          player on your roster (no injury or lineup knowledge) from the day after the report date
          through the end of the fantasy week. Slots you already locked are read from
          nba_lock_decisions (record them with scripts/record_lock.py); --filled / --locked still work as overrides.
Starters ('S') are the players in your lineup now.

Run (from project root):
    python scripts/my_lock_report.py                 (yesterday, roster 8)
    python scripts/my_lock_report.py --date 2026-10-20
    python scripts/my_lock_report.py --filled PG,UTIL   (slots you have already locked this week)
    python scripts/my_lock_report.py --locked Mitchell=PG,Zubac=C   (same, naming the players; also hides them from the advice)
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
  -- not players you picked up AFTER that game (you could not lock a score from before you owned him)
  AND NOT EXISTS (SELECT 1 FROM sleeper_transactions t
                  WHERE t.league_id = ro.league_id AND t.status = 'complete'
                    AND t.adds ->> ro.sleeper_player_id = ro.roster_id::text
                    -- waivers process overnight AFTER the claim is placed, so a claim made on or after the game's day
                    -- was not yours yet; other adds (trades) take effect when created
                    AND ((COALESCE(t.type, '') = 'waiver' AND t.created::date >= e.game_date)
                         OR (COALESCE(t.type, '') <> 'waiver' AND t.created::date > e.game_date)))
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
    if str(row.get("slot") or "").startswith("locked"):
        return "LOCKED"   # already locked into a slot: no decision left (the model signal is still in row["signal"])
    return row["signal"] if row["signal"] else "no signal"


RATE_SQL = "SELECT tier, games_left, above_bar, n, lock_was_right FROM nba_lock_win_rate"


def gap_bucket(gap):
    """Same cut points as the above_bar buckets in models/nba_lock_win_rate.sql."""
    return "a 0-5" if gap < 5 else "b 5-10" if gap < 10 else "c 10-15" if gap < 15 else "d 15+"


def apply_lock_right(conn, rows):
    """For LOCK rows replace lock_pct with the historical rate that locking a score this far above the bar was
    right. Returns False if the view is missing, in which case rows keep the old percentage_to_lock."""
    cur = conn.cursor()
    try:
        cur.execute(RATE_SQL)
        table = {(t, int(k), b): (int(n), float(rate)) for t, k, b, n, rate in cur.fetchall()}
    except Exception:
        conn.rollback()
        return False
    finally:
        cur.close()
    for r in rows:
        if r["signal"] != "LOCK" or r["bar"] is None:
            continue
        k = int(round(r["left"]))
        if k <= 0:
            r["lock_pct"] = 1.0
            continue
        hit = table.get((r["tier"], min(k, 3), gap_bucket(r["score"] - r["bar"])))
        if hit:
            r["hist_n"], r["lock_pct"] = hit
        else:
            r["lock_pct"] = None
    return True


def format_rows(rows):
    lines = [f"{'':1} {'Player':<24} {'Score':>6} {'Bar':>6} {'Gap':>6} {'Left':>5} {'Lock right':>10}  {'Signal':<10} {'Lock into':<10} Note"]
    lines.append("-" * 108)
    for r in rows:
        bar = f"{r['bar']:.2f}" if r["bar"] is not None else "-"
        gap = f"{r['score'] - r['bar']:+.2f}" if r["bar"] is not None else "-"
        pct = f"{r['lock_pct']*100:.0f}%" if r["lock_pct"] is not None else "-"
        note = ""
        if not r["signal"]:
            note = "no baseline yet (rookie / under 20 games)"
        elif r["source"] and r["source"] != "season":
            note = f"bar from {r['source']} stats"
        if r.get("hist_n"):
            note = (note + "; " if note else "") + f"{r['hist_n']} similar past locks"
        lines.append(f"{'S' if r['starter'] else ' ':1} {r['name']:<24} {r['score']:>6.2f} {bar:>6} {gap:>6} "
                     f"{r['left']:>5.1f} {pct:>10}  {signal_label(r):<10} {r.get('slot') or '':<10} {note}")
    return lines


def recommend_slots(conn, rows, args, day):
    """Fills r['slot'] for LOCK rows; returns (ctx_ok, notes, supply)."""
    from waiver_bid_advisor import fetch_schedule_context, fetch_player_teams, fetch_taxi, POSITIONS_SQL
    from lineup_fit import slot_supply, recommend_lock_slots, SLOTS

    cur0 = conn.cursor()
    cur0.execute("SELECT league_id FROM sleeper_current_league")
    taxi = fetch_taxi(conn, cur0.fetchone()[0]).get(args.roster_id, set())
    cur0.execute(ROSTER_SQL, {"roster": args.roster_id})
    roster = cur0.fetchall()
    cur0.close()

    # --locked "Mitchell=PG,Zubac=C": players you already locked into a slot. Their slot is used up and their
    # remaining games no longer matter, so they are left out of the advice and out of the games-left counts.
    notes, locked_by_sid = [], {}
    # Locks recorded with scripts/record_lock.py (table nba_lock_decisions) for this fantasy week count automatically.
    try:
        cur0 = conn.cursor()
        cur0.execute("""SELECT d.sleeper_player_id, d.player_name, d.slot FROM nba_lock_decisions d
                       JOIN fantasy_weeks w ON w.season_id = d.season_id AND w.week_number = d.week_number
                       WHERE d.roster_id = %s AND d.season_id = %s AND %s BETWEEN w.week_start_date AND w.week_end_date""",
                     (args.roster_id, args.season_id, day))
        for sid, nm, sl in cur0.fetchall():
            locked_by_sid[sid] = (nm, sl.rstrip("12") if sl.startswith("UTIL") else sl)
        cur0.close()
        if locked_by_sid:
            notes.append(f"{len(locked_by_sid)} lock(s) read from nba_lock_decisions")
    except Exception:
        conn.rollback()
    for item in (args.locked.split(",") if args.locked else []):
        if "=" not in item:
            notes.append(f"--locked '{item.strip()}' ignored: use NAME=SLOT, e.g. Mitchell=PG")
            continue
        frag, slot = (x.strip() for x in item.split("=", 1))
        slot = slot.upper()
        hits = [(sid, nm) for sid, _nba, nm in roster if frag.lower() in nm.lower()]
        if slot not in SLOTS:
            notes.append(f"--locked '{item.strip()}' ignored: '{slot}' is not one of {sorted(set(SLOTS))}")
        elif len(hits) != 1:
            notes.append(f"--locked '{item.strip()}' ignored: '{frag}' matched {len(hits)} players on your roster")
        else:
            locked_by_sid[hits[0][0]] = (hits[0][1], slot)
    for r in rows:
        if r["sid"] in locked_by_sid:
            r["slot"] = f"locked {locked_by_sid[r['sid']][1]}"
    if locked_by_sid:
        notes.append("Already locked: " + ", ".join(f"{nm}={sl}" for nm, sl in locked_by_sid.values()))

    lockers = [r for r in rows if r["signal"] == "LOCK" and r["sid"] not in taxi and r["sid"] not in locked_by_sid]
    if not lockers:
        return True, notes, {}
    ctx = fetch_schedule_context(conn, args.season_id, day, None)
    if ctx is None:
        return False, notes + ["Slot advice skipped: that date is not inside a fantasy week."], {}
    cur = conn.cursor()
    cur.execute(POSITIONS_SQL)
    positions = {sid: list(ps) for sid, ps in cur.fetchall()}
    cur.close()
    teams = fetch_player_teams(conn, [nba for _sid, nba, _nm in roster])
    locker_ids = {r["nba_id"] for r in lockers}
    candidates = []
    for sid, nba, _nm in roster:
        if nba in locker_ids or sid in taxi or sid in locked_by_sid:
            continue
        team_id = teams.get(nba, (None, None))[0]
        dates = ctx["games"].get(team_id, set()) if team_id else set()
        left = sum(1 for d in dates if day < d <= ctx["end"])
        candidates.append((positions.get(sid, []), left))
    supply = slot_supply(candidates)
    filled = [x.strip().upper() for x in args.filled.split(",") if x.strip()] if args.filled else []
    filled += [sl for _nm, sl in locked_by_sid.values()]
    rec = recommend_lock_slots(
        [{"name": r["name"], "positions": positions.get(r["sid"], []), "gap": r["score"] - (r["bar"] or 0)} for r in lockers],
        supply, filled)
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
    ap.add_argument("--locked", help="players you already locked, as NAME=SLOT comma separated, e.g. Mitchell=PG,Zubac=C "
                                     "(their slot counts as filled and they drop out of the advice)")
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
    rate_ok = apply_lock_right(conn, rows) if rows else True
    if rows:
        _ok, notes, supply = recommend_slots(conn, rows, args, date.fromisoformat(day))
    conn.close()

    print(f"\nLock report for roster {args.roster_id}, games on {day} ({len(rows)} played)")
    if not rows:
        print("No roster players have a game on that date. Has the daily load run for it?")
        return
    print("\n".join(format_rows(rows)))
    counts = {k: sum(1 for r in rows if signal_label(r) == k) for k in ("LOCK", "LOCKED", "HOLD", "PASS", "no signal")}
    if notes:
        print("\nWhere to lock:")
        for n in notes:
            print("  " + n)
        if supply:
            order = ["PG", "SG", "SF", "PF", "C", "G", "F", "UTIL"]
            print("  Games left for the rest of your roster, by slot: " + ", ".join(f"{k} {supply[k]:g}" for k in order if k in supply))
    print(f"\nLOCK {counts['LOCK']} (open), LOCKED {counts['LOCKED']} (done), HOLD {counts['HOLD']}, PASS {counts['PASS']}, no signal {counts['no signal']}")
    if sat:
        print(f"Did not play ({len(sat)}): {', '.join(sat)}")
    print("LOCK = score at or above his lock bar (LOCKED = already locked into a slot). PASS = below it with no games left. HOLD = below it, games left.")
    print("Lock into: position slots before G/F before UTIL; among eligible ones, the slot with the fewest games left for the rest of your roster.")
    print("Assumes every slot is open unless you pass --filled; counts every rostered player except taxi (no injury knowledge).")
    print("Lock right: LOCK rows = how often a score this far above the bar was not beaten later that week (history);"
          " HOLD/PASS rows = the fitted below-bar curve (tier and games left). The signal itself comes from Score vs Bar.")
    if not rate_ok:
        print("NOTE: view nba_lock_win_rate not found, so LOCK rows still show the old curve value (not meaningful for locks)."
              " Create it: psql -d postgres -f models/nba_lock_win_rate.sql")


if __name__ == "__main__":
    main()
