"""Price a draft slot over many classes by using the NBA draft as a stand-in for the league's rookie draft. Read-only.

The league drafts 3 rounds of 10 = 30 picks, the size of an NBA first round, so league pick N is treated as NBA pick N (league
round 1 = NBA picks 1-10, round 2 = 11-20, round 3 = 21-30). That gives every class from 2014 on, and up to five seasons per pick,
where pick_value.py only has the two real drafts. Like pick_value.py, a pick is worth what it adds ABOVE the replacement line
(the average of the players ranked 91-110 that season, roughly what the waiver wire hands out); a pick below the line, below
--min-games, or one that never played adds 0.

Everything here comes from nba_player_season_minutes (season totals, est_fantasy_points = ESTIMATE without game bonuses and
technicals), for the line and for the players alike, so it is internally consistent but not comparable to the exact scores in
pick_value.py. Only FINISHED seasons count.

First it checks the stand-in: for the real 2025 and 2026 league drafts it prints how closely league pick order follows NBA pick
order (rank correlation) and, for 2025, which of the two predicted first-season output better. A correlation beyond
about +-1.96/sqrt(n-1) is distinguishable from chance (n = picks compared). Fantasy managers move players with immediate minutes up
the board, so the stand-in tends to flatter LATE slots: if late slots still look empty here, that is a stronger result.

Needs: scripts/ingestion/load_nba_draft_history.py, scripts/ingestion/load_player_season_minutes.py, scripts/sleeper/ingest_drafts.py.

Usage (from ~/projects/two_words):  python scripts/pick_value_proxy.py [--line-start 91] [--line-end 110] [--min-games 20] [--slots]
  --slots   also list each of the 30 slots (10 classes per slot: noisy, for reference)
"""
import argparse
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))
from pick_value import band, replacement_line   # same line and same bands as the league-draft report

YEARS = (1, 2, 3, 4, 5)


def ranks_avg(xs):
    """Ranks 1..n with ties sharing their average rank."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def spearman(xs, ys):
    """Rank correlation, or None with fewer than 3 pairs or no spread."""
    if len(xs) < 3:
        return None
    rx, ry = ranks_avg(xs), ranks_avg(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxx = sum((a - mx) ** 2 for a in rx)
    syy = sum((b - my) ** 2 for b in ry)
    if sxx == 0 or syy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / math.sqrt(sxx * syy)


def season_value(stat, line, min_games):
    """(fpg, vor) for one season row (games, fpg) against that season's line; below min games or no row -> (fpg, 0)."""
    if stat is None:
        return None, 0.0
    games, fpg = stat
    if games < min_games or line is None:
        return fpg, 0.0
    return fpg, max(0.0, fpg - line)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--line-start", type=int, default=91)
    ap.add_argument("--line-end", type=int, default=110)
    ap.add_argument("--min-games", type=int, default=20)
    ap.add_argument("--slots", action="store_true")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    for t in ("nba_draft_history", "nba_player_season_minutes"):
        cur.execute("SELECT to_regclass(%s)", (t,))
        if cur.fetchone()[0] is None:
            raise SystemExit(f"{t} is missing: run scripts/ingestion/load_nba_draft_history.py and load_player_season_minutes.py first")
    cur.execute("SELECT season_id FROM current_season_config WHERE id = 1")
    current = cur.fetchone()[0]
    cur.execute("""SELECT season_id, player_id, games, est_fantasy_points FROM nba_player_season_minutes
                   WHERE season_id < %s AND games > 0""", (current,))
    stats = defaultdict(dict)   # season_id -> {player: (games, est fp per game)}
    for sid, pid, g, fp in cur.fetchall():
        stats[sid][pid] = (int(g), float(fp or 0) / int(g))
    cur.execute("SELECT draft_year, overall_pick, player_id, player_name FROM nba_draft_history WHERE overall_pick <= 30 ORDER BY 1, 2")
    draft = cur.fetchall()
    cur.execute("""SELECT d.season, p.pick_no, p.nba_player_id FROM sleeper_draft_picks p JOIN sleeper_drafts d USING (draft_id)
                   WHERE d.season IN ('2025', '2026') AND d.rounds = 3 AND p.nba_player_id IS NOT NULL""")
    league = cur.fetchall()
    cur.close(); conn.close()
    if not draft:
        raise SystemExit("nba_draft_history is empty")
    last_done = max(int(s[1:]) for s in stats)   # start year of the latest finished season

    lines = {sid: replacement_line({p: v[1] for p, v in pl.items() if v[0] >= args.min_games}, args.line_start, args.line_end)[0]
             for sid, pl in stats.items()}

    # --- how closely does league order follow NBA order?
    nba_pick = {(y, pid): pk for y, pk, pid, _n in draft}
    print("Does the NBA draft order stand in for the league's draft order?")
    first_season = {}
    for season in ("2025", "2026"):
        pairs = [(pk, nba_pick[(int(season), pid)], pid) for s, pk, pid in league if s == season and (int(season), pid) in nba_pick]
        r = spearman([a for a, _b, _p in pairs], [b for _a, b, _p in pairs])
        if r is None:
            print(f"  {season}: not enough matched picks")
            continue
        print(f"  {season}: {len(pairs)} of the league's 30 picks were NBA first-rounders; league pick vs NBA pick rank correlation {r:+.2f} "
              f"(chance-level band +-{1.96 / math.sqrt(len(pairs) - 1):.2f})")
        first_season[season] = pairs
    pairs = first_season.get("2025")
    sid1 = "22025"
    if pairs and sid1 in stats:
        fpg = [stats[sid1].get(pid, (0, 0.0))[1] for _a, _b, pid in pairs]
        r_league = spearman([a for a, _b, _p in pairs], [-x for x in fpg])
        r_nba = spearman([b for _a, b, _p in pairs], [-x for x in fpg])
        if r_league is not None and r_nba is not None:
            print(f"  2025 class, first-season fantasy points/g (never played = 0): league pick order {r_league:+.2f}, NBA pick order {r_nba:+.2f}"
                  f" ({'league' if r_league > r_nba else 'NBA'} order predicted it better)")

    # --- the proxy over every finished class
    picks = []   # (slot, class, [(fpg, vor) or None per year])
    for y, pk, pid, _name in draft:
        yrs = []
        for k in YEARS:
            sid = f"2{y + k - 1}"
            yrs.append(None if y + k - 1 > last_done or sid not in stats else season_value(stats[sid].get(pid), lines.get(sid), args.min_games))
        if yrs[0] is not None:
            picks.append((pk, y, yrs))
    classes = sorted({y for _s, y, _v in picks})
    print(f"\nNBA classes {classes[0]}-{classes[-1]} as the stand-in for the league draft; replacement line (ranks {args.line_start}-{args.line_end}, "
          f"est. fantasy points/g): " + ", ".join(f"{s[1:]}: {lines[s]:.1f}" for s in sorted(lines) if lines[s]))

    order = ["R1 early", "R1 mid", "R1 late", "R2 early", "R2 mid", "R2 late", "R3+"]
    def key(slot):
        return band((slot - 1) % 10 + 1, 10, (slot - 1) // 10 + 1)

    def table(label, groups):
        print(f"\n{label:<10}{'picks':>6}" + "".join(f"{'yr' + str(k) + ' clear':>10}{'yr' + str(k) + ' vor':>9}" for k in YEARS)
              + f"{'3-yr total':>12}{'5-yr total':>12}")
        for name, grp in groups:
            if not grp:
                continue
            cells = ""
            for i in range(len(YEARS)):
                ys = [v[i] for _s, _y, v in grp if v[i] is not None]
                cells += f"{'':>10}{'':>9}" if not ys else f"{100.0 * sum(1 for _f, vv in ys if vv > 0) / len(ys):>9.0f}%{sum(vv for _f, vv in ys) / len(ys):>9.2f}"
            def total(n):
                full = [v for _s, _y, v in grp if all(x is not None for x in v[:n])]
                return f"{sum(sum(vv for _f, vv in v[:n]) for v in full) / len(full):>12.1f}" if full else f"{'':>12}"
            print(f"{name:<10}{len(grp):>6}{cells}{total(3)}{total(5)}")

    table("band", [(b, [p for p in picks if key(p[0]) == b]) for b in order])
    if args.slots:
        table("slot", [(f"pick {s}", [p for p in picks if p[0] == s]) for s in range(1, 31)])
    full3 = sum(1 for p in picks if all(x is not None for x in p[2][:3]))
    full5 = sum(1 for p in picks if all(x is not None for x in p[2][:5]))
    print(f"\nclear = share above the line (never played, or under {args.min_games} games = miss). vor = average points/g above the line, 0 when below.")
    print(f"3-yr total = points/g above the line summed over seasons 1-3, averaged over picks with all three seasons finished ({full3} picks); "
          f"5-yr total the same over five seasons ({full5} picks). Read the totals as 'points per game of lineup edge over the waiver line, "
          f"added up across years'.")


if __name__ == "__main__":
    main()
