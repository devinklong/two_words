"""Check a finished fantasy week: for every team, does each slot's Sleeper points equal one real game by that player, and which one?

Sleeper's rules say a starter you never locked is auto-locked to his FINAL game of the week. This reads each roster's live matchup (starters in
slot order + points per slot), looks up the player's games that week in game_fantasy_scores_weekly_effective, and labels each slot:
  last   points equal his last game of the week        first  equal his first game       best  equal his best game (not first/last)
  other  equal one of his games, none of the above     none   equal none of his games (a sum, a stat correction, a missing game, or a zero)
It also checks that the slot points add up to the team's total and prints the matchup result. Read-only; nothing is written.

Usage (from ~/projects/two_words):  python scripts/check_week_scores.py [--week 1] [--season-id 22026] [--roster-id 8]
"""
import argparse
import json
import urllib.request
from datetime import date

SLOTS = ["PG", "SG", "G", "SF", "PF", "F", "C", "UTIL1", "UTIL2"]
OWNERS = {1: "Hendo64", 2: "Folger11", 3: "TommyTableSalsa", 4: "SeanKelly13", 5: "cocohebbles",
          6: "Pete1771", 7: "Crash374", 8: "sweetdiddlydee", 9: "CountyShirriff", 10: "Yaak0v"}
TOL = 0.011


def classify(points, games):
    """games: [(date, score)] for the week. Returns a label and the matching date (or None)."""
    if not games:
        return "no games", None
    if points is None:
        return "none", None
    hits = [(d, s) for d, s in games if abs(float(s) - float(points)) <= TOL]
    if not hits:
        return "none", None
    ordered = sorted(games)
    last, first = ordered[-1], ordered[0]
    d = hits[-1][0]
    if abs(float(last[1]) - float(points)) <= TOL:
        return "last", last[0]
    if abs(float(first[1]) - float(points)) <= TOL:
        return "first", first[0]
    return ("best" if abs(max(float(s) for _d, s in games) - float(points)) <= TOL else "other"), d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, default=1)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--roster-id", type=int, help="only this roster")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    try:
        cur.execute("SELECT sleeper_player_id, nba_player_id FROM sleeper_player_crosswalk WHERE nba_player_id IS NOT NULL")
    except Exception:
        conn.rollback()
        cur.execute("SELECT DISTINCT sleeper_player_id, nba_player_id FROM roster_ownership WHERE nba_player_id IS NOT NULL")
    xw = dict(cur.fetchall())
    cur.execute("SELECT player_id, game_date, fantasy_score FROM game_fantasy_scores_weekly_effective WHERE season_id = %s AND week_number = %s",
                (args.season_id, args.week))
    games = {}
    for pid, d, sc in cur.fetchall():
        games.setdefault(pid, []).append((d, sc))
    cur.execute("SELECT DISTINCT sleeper_player_id, player_name FROM roster_ownership WHERE player_name IS NOT NULL")
    names = dict(cur.fetchall())
    conn.close()

    data = json.load(urllib.request.urlopen(f"https://api.sleeper.app/v1/league/{league}/matchups/{args.week}"))
    by_matchup = {}
    for m in data:
        by_matchup.setdefault(m.get("matchup_id"), []).append(m)
    totals = {"last": 0, "first": 0, "best": 0, "other": 0, "none": 0, "no games": 0}
    for m in sorted(data, key=lambda x: x["roster_id"]):
        rid = m["roster_id"]
        if args.roster_id and rid != args.roster_id:
            continue
        starters, spts = m.get("starters") or [], m.get("starters_points") or []
        opp = [o for o in by_matchup.get(m.get("matchup_id"), []) if o["roster_id"] != rid]
        res = ""
        if opp and m.get("points") is not None:
            res = " WIN" if m["points"] > opp[0]["points"] else (" LOSS" if m["points"] < opp[0]["points"] else " TIE")
            res += f" vs {OWNERS.get(opp[0]['roster_id'], opp[0]['roster_id'])} {opp[0]['points']}"
        add_up = round(sum(p or 0 for p in spts), 2)
        flag = "" if m.get("points") is None or abs(add_up - float(m["points"])) < 0.02 else f"  !! slots add to {add_up}"
        print(f"\nRoster {rid} {OWNERS.get(rid, '')}: {m.get('points')}{res}{flag}")
        for i, (sid, pts) in enumerate(zip(starters, spts)):
            if sid in ("0", None, 0):
                print(f"  {SLOTS[i]:<6} (empty slot)  {pts}")
                continue
            label, when = classify(pts, games.get(xw.get(sid), []))
            totals[label] = totals.get(label, 0) + 1
            gl = ", ".join(f"{d:%m/%d} {float(s):.2f}" for d, s in sorted(games.get(xw.get(sid), [])))
            print(f"  {SLOTS[i]:<6} {names.get(sid, sid):<24} {pts if pts is not None else '-':>7}  {label:<8} {when or '':<11} [{gl}]")
    print("\nSlot summary: " + ", ".join(f"{k} {v}" for k, v in totals.items() if v))
    print("last = auto-locked final game (or locked on it); none = points match no single game (look at these first).")


if __name__ == "__main__":
    main()
