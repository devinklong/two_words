"""Record (or list / remove) the locks you made, so the lock report knows which slots are already used.

A lock = one player's score from one game, frozen into one lineup slot for the fantasy week. Each record also
stores what the model said at the time (bar, signal, tier, games left) for later analysis.

Usage (from ~/projects/two_words):
    python scripts/record_lock.py Mitchell=PG --date 2026-10-22     (the date of the GAME you locked)
    python scripts/record_lock.py Zubac=C Mitchell=PG --date 2026-10-21
    python scripts/record_lock.py --list [--week 1]                  (what is recorded for the week)
    python scripts/record_lock.py --remove PG --week 1               (undo a mistaken record)
    --replace   overwrite a slot that is already recorded     --force   record even if he is not eligible for the slot
UTIL goes to UTIL1, then UTIL2 (or name UTIL1 / UTIL2 yourself). --roster-id defaults to 8 (sweetdiddlydee).
"""
import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

MY_ROSTER_ID = 8
SLOT_LABELS = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL1", "UTIL2"]

LEAGUE_SQL = "SELECT league_id FROM sleeper_current_league"
ROSTER_SQL = """
SELECT sleeper_player_id, nba_player_id, player_name FROM roster_ownership
WHERE roster_id = %(roster)s AND league_id = %(league)s
"""
POSITIONS_SQL = "SELECT array_agg(position) FROM sleeper_player_fantasy_positions WHERE sleeper_player_id = %s"
GAME_SQL = """
SELECT e.game_id, e.fantasy_score, e.effective_games_remaining_in_week, e.week_number, e.season_id,
       s.lock_bar, s.lock_signal, s.percentage_to_lock, s.tier, s.player_stats_source
FROM game_fantasy_scores_weekly_effective e
LEFT JOIN game_lock_signal s ON s.game_id = e.game_id AND s.player_id = e.player_id
WHERE e.player_id = %(nba)s AND e.game_date = %(day)s AND e.season_id = %(season)s
"""
USED_SQL = """
SELECT slot, player_name, game_date FROM nba_lock_decisions
WHERE league_id = %(league)s AND roster_id = %(roster)s AND week_number = %(week)s ORDER BY slot
"""
UPSERT_SQL = """
INSERT INTO nba_lock_decisions (league_id, roster_id, season_id, week_number, slot, sleeper_player_id, nba_player_id,
    player_name, game_id, game_date, fantasy_score, lock_bar, model_signal, model_percentage_to_lock, tier,
    stats_source, games_left_effective, source)
VALUES (%(league)s, %(roster)s, %(season)s, %(week)s, %(slot)s, %(sid)s, %(nba)s, %(name)s, %(game_id)s, %(day)s,
    %(score)s, %(bar)s, %(signal)s, %(pct)s, %(tier)s, %(src)s, %(left)s, 'manual')
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("locks", nargs="*", help="NAME=SLOT, e.g. Mitchell=PG")
    ap.add_argument("--date", help="date of the game that was locked, YYYY-MM-DD (default: yesterday, America/Chicago)")
    ap.add_argument("--roster-id", type=int, default=MY_ROSTER_ID)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--week", type=int, help="fantasy week (for --list / --remove)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--from-sleeper", metavar="NAMES", help="comma-separated names you have locked: reads each one's slot and score from "
                    "your Sleeper lineup and finds the game by its score (no --date needed). Use --week N for a past week.")
    ap.add_argument("--remove", metavar="SLOT")
    ap.add_argument("--replace", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    from db_connection import get_connection
    from lineup_fit import eligible
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT to_regclass('nba_lock_decisions')")
    if cur.fetchone()[0] is None:
        raise SystemExit("Table nba_lock_decisions is missing. Create it: psql -d postgres -f schema/tables/nba_lock_decisions.sql")
    cur.execute(LEAGUE_SQL)
    league = cur.fetchone()[0]
    day = args.date or (datetime.now(ZoneInfo("America/Chicago")).date() - timedelta(days=1)).isoformat()

    if args.list or args.remove:
        week = args.week
        if week is None:
            cur.execute("SELECT week_number FROM fantasy_weeks WHERE season_id = %s AND %s BETWEEN week_start_date AND week_end_date",
                        (args.season_id, date.today()))
            row = cur.fetchone()
            week = row[0] if row else None
        if week is None:
            raise SystemExit("Could not work out the week; pass --week N.")
        if args.remove:
            slot = args.remove.upper()
            cur.execute("DELETE FROM nba_lock_decisions WHERE league_id=%s AND roster_id=%s AND week_number=%s AND slot=%s",
                        (league, args.roster_id, week, slot))
            print(f"Removed {cur.rowcount} record(s) for {slot}, week {week}.")
            conn.commit()
        cur.execute(USED_SQL, {"league": league, "roster": args.roster_id, "week": week})
        rows = cur.fetchall()
        print(f"Locks recorded for roster {args.roster_id}, week {week}: {len(rows)} of {len(SLOT_LABELS)} slots")
        for slot, name, gd in rows:
            print(f"  {slot:<6} {name}  (game {gd})")
        open_slots = [s for s in SLOT_LABELS if s not in {r[0] for r in rows}]
        print("  still open: " + (", ".join(open_slots) or "none"))
        return

    if not args.locks and not args.from_sleeper:
        raise SystemExit("Nothing to record. Example: python scripts/record_lock.py Mitchell=PG --date 2026-10-22\n"
                         "or:  python scripts/record_lock.py --from-sleeper Castle,Mitchell,Giannis")

    cur.execute(ROSTER_SQL, {"roster": args.roster_id, "league": league})
    roster = cur.fetchall()
    problems = 0
    plan = [(i, day) for i in args.locks]
    if args.from_sleeper:
        import json, urllib.request
        week_n = args.week
        if week_n is None:
            cur.execute("SELECT week_number FROM fantasy_weeks WHERE season_id = %s AND %s BETWEEN week_start_date AND week_end_date",
                        (args.season_id, date.today()))
            row = cur.fetchone()
            week_n = row[0] if row else None
        if week_n is None:
            raise SystemExit("Could not work out the week; pass --week N.")
        data = json.load(urllib.request.urlopen(f"https://api.sleeper.app/v1/league/{league}/matchups/{week_n}"))
        mine = [m for m in data if m.get("roster_id") == args.roster_id]
        if not mine or not mine[0].get("starters"):
            raise SystemExit(f"No lineup found for roster {args.roster_id}, week {week_n}.")
        starters, spts = mine[0]["starters"], mine[0]["starters_points"]
        if len(starters) != len(SLOT_LABELS):
            raise SystemExit(f"Expected {len(SLOT_LABELS)} starters, found {len(starters)}.")
        for frag in [f.strip() for f in args.from_sleeper.split(",") if f.strip()]:
            hits = [r for r in roster if frag.lower() in (r[2] or "").lower()]
            if len(hits) != 1:
                print(f"SKIPPED '{frag}': matched {len(hits)} players on roster {args.roster_id}"); problems += 1; continue
            sid, nba, name = hits[0]
            if sid not in starters:
                print(f"SKIPPED '{frag}': {name} is not in a starting slot in week {week_n}"); problems += 1; continue
            idx = starters.index(sid)
            pts = spts[idx]
            cur.execute("SELECT game_date FROM game_fantasy_scores_weekly_effective WHERE player_id = %s AND season_id = %s "
                        "AND week_number = %s AND ABS(fantasy_score - %s) <= 0.011 ORDER BY game_date", (nba, args.season_id, week_n, pts))
            dates = [r[0] for r in cur.fetchall()]
            if len(dates) != 1:
                print(f"SKIPPED '{frag}': {name}'s slot shows {pts}, which matches {len(dates)} of his games this week "
                      f"(use NAME=SLOT --date instead)"); problems += 1; continue
            plan.append((f"{frag}={SLOT_LABELS[idx]}", dates[0].isoformat()))
            print(f"  {name}: slot {SLOT_LABELS[idx]}, {pts} -> game {dates[0]}")
    for item, day in plan:
        if "=" not in item:
            print(f"SKIPPED '{item}': use NAME=SLOT, e.g. Mitchell=PG"); problems += 1; continue
        frag, slot = (x.strip() for x in item.split("=", 1))
        slot = slot.upper()
        hits = [r for r in roster if frag.lower() in (r[2] or "").lower()]
        if len(hits) != 1:
            print(f"SKIPPED '{item}': '{frag}' matched {len(hits)} players on roster {args.roster_id}"); problems += 1; continue
        sid, nba, name = hits[0]
        if nba is None:
            print(f"SKIPPED '{item}': {name} has no nba_player_id yet, so his game score cannot be looked up"); problems += 1; continue
        base = slot.rstrip("12") if slot.startswith("UTIL") else slot
        if base not in {s.rstrip("12") if s.startswith("UTIL") else s for s in SLOT_LABELS}:
            print(f"SKIPPED '{item}': '{slot}' is not a slot ({', '.join(SLOT_LABELS)})"); problems += 1; continue
        cur.execute(GAME_SQL, {"nba": nba, "day": day, "season": args.season_id})
        g = cur.fetchone()
        if g is None:
            print(f"SKIPPED '{item}': {name} has no game on {day}. Pass --date for the day of the game you locked."); problems += 1; continue
        game_id, score, left, week, season, bar, signal, pct, tier, src = g
        cur.execute(USED_SQL, {"league": league, "roster": args.roster_id, "week": week})
        used = {r[0]: r for r in cur.fetchall()}
        if slot == "UTIL":
            free = [s for s in ("UTIL1", "UTIL2") if s not in used]
            slot = free[0] if free else "UTIL1"
        cur.execute(POSITIONS_SQL, (sid,))
        positions = (cur.fetchone() or [None])[0] or []
        if positions and not args.force and not eligible(positions, base):
            print(f"SKIPPED '{item}': {name} ({'/'.join(positions)}) is not eligible for {base}. Use --force to record anyway.")
            problems += 1; continue
        if slot in used and not args.replace:
            print(f"SKIPPED '{item}': {slot} already has {used[slot][1]} recorded for week {week}. Use --replace to overwrite.")
            problems += 1; continue
        if slot in used:
            cur.execute("DELETE FROM nba_lock_decisions WHERE league_id=%s AND roster_id=%s AND week_number=%s AND slot=%s",
                        (league, args.roster_id, week, slot))
        again = [v[0] for v in used.values() if v[1] == name and v[0] != slot]
        cur.execute(UPSERT_SQL, {"league": league, "roster": args.roster_id, "season": season, "week": week, "slot": slot,
                                 "sid": sid, "nba": nba, "name": name, "game_id": game_id, "day": day,
                                 "score": score, "bar": bar, "signal": signal, "pct": pct, "tier": tier, "src": src,
                                 "left": left})
        conn.commit()
        extra = f"  (note: {name} is also recorded in {', '.join(again)} this week)" if again else ""
        print(f"Recorded: {name} {float(score):.2f} on {day} -> {slot}, week {week}. "
              f"Model said {signal or 'no signal'} (bar {float(bar):.2f})." if bar is not None else
              f"Recorded: {name} {float(score):.2f} on {day} -> {slot}, week {week}. Model had no signal.")
        if extra: print(extra)
    cur.close()
    conn.close()
    if problems:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
