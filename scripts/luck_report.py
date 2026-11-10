"""Was a team's record luck or strength? Read-only. Uses Sleeper's recorded matchup points (historical_matchup_results), closed regular-season weeks only.

For every week each team is also scored against EVERY other team that week (all-play): beating 6 of the 9 others is a 0.67 win, whatever
the real opponent did. Summed over the season that is the record the team's scoring deserved.

  W-L        the real record
  exp W      all-play wins: what the scoring earned (expected wins)
  luck       W minus exp W: positive = the schedule helped, negative = it hurt (about +-1.5 wins is ordinary over a full season)
  PF/wk, PA/wk   points scored and allowed per week; PF rank is where the scoring stands in the league
  PA vs lg   points allowed per week minus the league average per week: positive = opponents scored more against you than against a typical team

The current season is short (a few weeks), so its luck is noisy: three games can swing 1 to 1.5 wins by chance alone. The projection line
assumes your all-play rate so far holds for the rest of the season and compares it with the wins the 6th-best team needed in past seasons.

Usage (from ~/projects/two_words):  python scripts/luck_report.py [--owner sweetdiddlydee] [--all]
  --all   print every team in every season (default: your row in each season, plus the current season in full)
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

REGULAR_WEEKS = 21
PLAYOFF_SPOTS = 6


def compute(rows):
    """rows = [(season, week, owner, points, opp_points)] -> {(season, owner): stats} from every closed regular-season week."""
    weeks = defaultdict(dict)   # (season, week) -> {owner: points}
    for season, week, owner, pts, _opp in rows:
        weeks[(season, week)][owner] = float(pts)
    out = defaultdict(lambda: {"w": 0, "l": 0, "t": 0, "exp": 0.0, "pf": 0.0, "pa": 0.0, "n": 0, "lg_pf": 0.0})
    for season, week, owner, pts, opp in rows:
        scores = weeks[(season, week)]
        others = [v for o, v in scores.items() if o != owner]
        if not others:
            continue
        s = out[(season, owner)]
        pts, opp = float(pts), float(opp)
        s["w"] += pts > opp
        s["l"] += pts < opp
        s["t"] += pts == opp
        s["exp"] += (sum(1 for v in others if pts > v) + 0.5 * sum(1 for v in others if pts == v)) / len(others)
        s["pf"] += pts
        s["pa"] += opp
        s["n"] += 1
        s["lg_pf"] += sum(scores.values()) / len(scores)   # the league's average team score that week
    return out


def cutoff_wins(stats, season):
    """Wins of the PLAYOFF_SPOTS-th best team in a finished season (the number needed to get in)."""
    wins = sorted((s["w"] + 0.5 * s["t"] for (se, _o), s in stats.items() if se == season), reverse=True)
    return wins[PLAYOFF_SPOTS - 1] if len(wins) >= PLAYOFF_SPOTS else None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", default="sweetdiddlydee")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SELECT season FROM sleeper_current_league")
    current = cur.fetchone()[0]
    cur.execute("""SELECT sl.season, h.week, h.owner_name, h.team_points, h.opponent_points
                   FROM historical_matchup_results h
                   JOIN sleeper_leagues sl ON sl.league_id = h.league_id
                   JOIN fantasy_weeks fw ON fw.season_id = '2' || sl.season AND fw.week_number = h.week
                   WHERE h.week <= %s AND h.result IS NOT NULL AND fw.week_end_date < current_date""", (REGULAR_WEEKS,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    if not rows:
        raise SystemExit("no closed matchup weeks found")

    stats = compute(rows)
    seasons = sorted({s for s, _o in stats})
    label = lambda s: f"{s}-{str(int(s) + 1)[-2:]}"

    def table(season, only=None):
        teams = [(o, s) for (se, o), s in stats.items() if se == season and (only is None or o == only)]
        teams.sort(key=lambda t: -t[1]["pf"])
        rank = {o: i + 1 for i, (o, _s) in enumerate(sorted(((o, s) for (se, o), s in stats.items() if se == season), key=lambda t: -t[1]["pf"]))}
        for o, s in teams:
            n = s["n"]
            rec = f"{int(s['w'])}-{int(s['l'])}" + (f"-{int(s['t'])}" if s["t"] else "")
            lg = s["lg_pf"] / n
            print(f"{('* ' if o == args.owner else '  ')}{o:<17}{rec:>8}{s['exp']:>8.1f}{s['w'] + 0.5 * s['t'] - s['exp']:>+8.1f}"
                  f"{s['pf'] / n:>9.1f}{rank[o]:>6}{s['pa'] / n - lg:>+10.1f}")

    head = f"  {'team':<17}{'W-L':>8}{'exp W':>8}{'luck':>8}{'PF/wk':>9}{'PF rk':>6}{'PA vs lg':>10}"
    for season in seasons:
        n = max(s["n"] for (se, _o), s in stats.items() if se == season)
        print(f"\n{label(season)}: {n} closed regular-season weeks" + (" (in progress)" if season == current else ""))
        print(head)
        table(season, None if (args.all or season == current) else args.owner)
        cut = cutoff_wins(stats, season)
        if season != current and cut is not None:
            print(f"  6th place finished with {cut:g} wins")

    mine = stats.get((current, args.owner))
    cuts = [cutoff_wins(stats, s) for s in seasons if s != current and max(x["n"] for (se, _o), x in stats.items() if se == s) >= REGULAR_WEEKS]
    if mine and cuts:
        n = mine["n"]
        rate = mine["exp"] / n
        proj = mine["w"] + 0.5 * mine["t"] + rate * (REGULAR_WEEKS - n)
        print(f"\nProjection: {args.owner} all-play rate {rate:.2f} over {n} weeks; if it holds, about {proj:.1f} wins in {REGULAR_WEEKS} "
              f"(6th place needed {', '.join(f'{c:g}' for c in cuts)} in past seasons).")
        print("Treat it as a rough marker: a few weeks of scoring is a noisy guide, and trades and injuries move it.")


if __name__ == "__main__":
    main()
