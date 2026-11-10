"""Standings, records and PF/PA computed from OUR verified weekly totals, set against Sleeper's official standings. Read-only.

Why: Sleeper's matchup points for a finished week drift (an unlocked slot follows the player's latest game, a slot can flicker to 0 for
a few minutes, and closed weeks have been seen changing days later). Records built on a single read of those points are not reliable.
infer_lock_decisions.py already stores each team's real week total in sleeper_roster_week_totals: the sum of the nine locked games,
solved so that the weeks add up to Sleeper's season total (fpts), which does not drift. This report plays every closed regular-season
week from those totals (paired with fantasy_matchups) and builds W-L-T, PF and PA from them.

Then it reads Sleeper's official standings (rosters endpoint: wins, losses, ties, fpts, fpts_against) and prints the difference:
  PF diff / PA diff   ours minus Sleeper's. Near 0 = our weekly totals add up to what Sleeper recorded.
  record              flagged when our W-L differs from Sleeper's: some game was decided on different weekly numbers than ours.
For every team whose record differs, its weeks are listed with both totals and the margin so the game in question is visible.

A league with no verified totals (a past season) falls back to Sleeper's final matchup points and says so. Only weeks that have ended
count (fantasy_weeks), so a week in progress never appears.

Usage (from ~/projects/two_words):  python scripts/standings.py [--league-id ID] [--no-official]
"""
import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

REGULAR_WEEKS = 21


def compute(totals, pairings, closed_weeks):
    """totals {(week, roster): points}, pairings [(week, roster, opponent)], closed_weeks set -> ({roster: stats}, games).
    stats = w, l, t, pf, pa, n. games = [(week, roster, points, opponent, opp_points, result)]; byes and weeks missing a side are skipped."""
    out = defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "pf": 0.0, "pa": 0.0, "n": 0})
    games = []
    for week, rid, opp in pairings:
        if week not in closed_weeks or opp is None:
            continue
        mine, theirs = totals.get((week, rid)), totals.get((week, opp))
        if mine is None or theirs is None:
            continue
        mine, theirs = float(mine), float(theirs)
        s = out[rid]
        s["pf"] += mine
        s["pa"] += theirs
        s["n"] += 1
        res = "W" if mine > theirs else ("L" if mine < theirs else "T")
        s[res.lower()] += 1
        games.append((week, rid, mine, opp, theirs, res))
    return out, games


def official_standings(league_id):
    """{roster: (wins, losses, ties, fpts, fpts_against)} from Sleeper's rosters endpoint."""
    data = json.load(urllib.request.urlopen(f"https://api.sleeper.app/v1/league/{league_id}/rosters", timeout=30))
    out = {}
    for r in data:
        s = r.get("settings") or {}
        out[r["roster_id"]] = (s.get("wins", 0), s.get("losses", 0), s.get("ties", 0),
                               s.get("fpts", 0) + s.get("fpts_decimal", 0) / 100, s.get("fpts_against", 0) + s.get("fpts_against_decimal", 0) / 100)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league-id", help="default: the current league")
    ap.add_argument("--no-official", action="store_true", help="skip the Sleeper comparison")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    if args.league_id:
        cur.execute("SELECT league_id, season FROM sleeper_leagues WHERE league_id = %s", (args.league_id,))
    else:
        cur.execute("SELECT league_id, season FROM sleeper_current_league")
    league, season = cur.fetchone()
    season_id = f"2{season}"
    cur.execute("""SELECT week_number FROM fantasy_weeks WHERE season_id = %s AND week_number <= %s AND week_end_date < current_date""",
                (season_id, REGULAR_WEEKS))
    closed = {w for (w,) in cur.fetchall()}
    cur.execute("""SELECT week_number, roster_id, final_points, status FROM sleeper_roster_week_totals
                   WHERE league_id = %s AND season_id = %s""", (league, season_id))
    rows = cur.fetchall()
    source = "verified weekly totals (sleeper_roster_week_totals)"
    if rows:
        totals = {(w, r): p for w, r, p, _s in rows}
        weak = [(w, r, s) for w, r, _p, s in rows if w in closed and s not in ("unique", "ok")]
    else:
        cur.execute("SELECT week, roster_id, points FROM sleeper_matchup_points_latest WHERE league_id = %s", (league,))
        totals = {(w, r): p for w, r, p in cur.fetchall() if p is not None}
        weak = []
        source = "Sleeper's final matchup points (no verified totals stored for this league)"
    cur.execute("SELECT week, roster_id, opponent_roster_id FROM fantasy_matchups WHERE league_id = %s", (league,))
    pairings = cur.fetchall()
    cur.execute("SELECT roster_id, current_owner_name FROM sleeper_roster_labels_current WHERE league_id = %s", (league,))
    names = dict(cur.fetchall())
    cur.close(); conn.close()

    stats, games = compute(totals, pairings, closed)
    if not stats:
        raise SystemExit("no closed weeks with totals for both teams")
    official = {} if args.no_official else official_standings(league)
    weeks = sorted({g[0] for g in games})
    print(f"{season}-{str(int(season) + 1)[-2:]} standings after week{'s' if len(weeks) > 1 else ''} {weeks[0]}-{weeks[-1]}, from {source}\n")
    print(f"{'rk':>3} {'team':<17}{'W-L':>7}{'PF':>10}{'PA':>10}" + (f"{'| official':>12}{'PF diff':>10}{'PA diff':>10}  " if official else ""))
    order = sorted(stats, key=lambda r: (-(stats[r]["w"] + 0.5 * stats[r]["t"]), -stats[r]["pf"]))
    differ = []
    for i, rid in enumerate(order, 1):
        s = stats[rid]
        rec = f"{s['w']}-{s['l']}" + (f"-{s['t']}" if s["t"] else "")
        line = f"{i:>3} {names.get(rid, rid):<17}{rec:>7}{s['pf']:>10.2f}{s['pa']:>10.2f}"
        if official and rid in official:
            ow, ol, ot, opf, opa = official[rid]
            orec = f"{ow}-{ol}" + (f"-{ot}" if ot else "")
            flag = "" if (ow, ol, ot) == (s["w"], s["l"], s["t"]) else "  <- record differs"
            if flag:
                differ.append(rid)
            line += f"{'| ' + orec:>12}{s['pf'] - opf:>+10.2f}{s['pa'] - opa:>+10.2f}{flag}"
        print(line)
    if weak:
        print("\nWeeks whose total is not a clean match to Sleeper's season total: "
              + ", ".join(f"week {w} roster {r} ({s})" for w, r, s in sorted(weak)))
    for rid in differ:
        print(f"\n{names.get(rid, rid)}: ours {stats[rid]['w']}-{stats[rid]['l']}, Sleeper {official[rid][0]}-{official[rid][1]}")
        for week, r, mine, opp, theirs, res in sorted(g for g in games if g[1] == rid):
            print(f"  week {week}: {mine:.2f} vs {names.get(opp, opp)} {theirs:.2f}  {res}  (margin {abs(mine - theirs):.2f})")
    if official and not differ:
        print("\nEvery record matches Sleeper's official standings.")


if __name__ == "__main__":
    main()
