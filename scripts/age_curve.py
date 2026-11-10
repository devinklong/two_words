"""NBA age curve from the SAME players in back-to-back seasons. Read-only; needs nba_player_season_minutes.

For every player with enough games at age N in one season and again the next season, take his change in minutes per game
and in estimated fantasy points per game. The average change at each age is the curve: positive = players that age are
still growing, negative = declining. Each player is compared with himself, so it is not skewed by which players are in the
league at each age (the problem with a plain "average minutes by age" table).

Columns
  pairs        players seen at this age and again the next season
  min/g        average change in minutes per game going from this age to the next (all seasons)
  early, late  the same, split into the earlier and the later half of the seasons, to see whether the curve is changing
  fp/g         average change in estimated fantasy points per game (see load_player_season_minutes.py: an estimate)
  gone         share of players at this age who did not play at all the next season. The curve cannot see them, so read
               a high 'gone' as extra decline on top of the minutes change.
  vs peak      running total of min/g, shifted so the best age is 0: how far a typical player's minutes sit below his peak

The season in progress is left out (a few weeks of games is not a season).

Usage (from ~/projects/two_words):  python scripts/age_curve.py [--min-games 20] [--min-pairs 15] [--csv out.csv]
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-games", type=int, default=20, help="games needed in BOTH seasons for a pair to count (default 20)")
    ap.add_argument("--min-pairs", type=int, default=15, help="ages with fewer pairs are marked * (default 15)")
    ap.add_argument("--csv")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SELECT to_regclass('nba_player_season_minutes')")
    if cur.fetchone()[0] is None:
        raise SystemExit("nba_player_season_minutes is missing: run  python scripts/ingestion/load_player_season_minutes.py  first")
    cur.execute("SELECT season_id FROM current_season_config WHERE id = 1")
    current = cur.fetchone()[0]
    cur.execute("""SELECT player_id, season_id, floor(age)::int, games, minutes, est_fantasy_points
                   FROM nba_player_season_minutes WHERE age IS NOT NULL AND season_id < %s""", (current,))
    rows = {}
    for pid, sid, age, games, minutes, fp in cur.fetchall():
        rows[(pid, int(sid[1:]))] = (age, int(games), float(minutes) / games, float(fp or 0) / games)
    cur.close(); conn.close()
    years = sorted({y for _p, y in rows})
    if len(years) < 2:
        raise SystemExit("need at least two finished seasons in nba_player_season_minutes")
    last = years[-1]
    start_years = years[:-1]
    split = start_years[len(start_years) // 2]   # pairs starting before this season are 'early'

    d_min, d_fp, early, late = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    seen, gone = defaultdict(int), defaultdict(int)
    for (pid, y), (age, games, mpg, fpg) in rows.items():
        if y == last or games < args.min_games:
            continue
        seen[age] += 1
        nxt = rows.get((pid, y + 1))
        if nxt is None:
            gone[age] += 1
            continue
        if nxt[1] < args.min_games:
            continue
        d_min[age].append(nxt[2] - mpg)
        d_fp[age].append(nxt[3] - fpg)
        (early if y < split else late)[age].append(nxt[2] - mpg)

    ages = sorted(a for a in d_min if d_min[a])
    avg = lambda xs: sum(xs) / len(xs) if xs else None
    running, level = 0.0, {}
    for a in ages:   # level[a] = minutes relative to the first age, at age a (before applying a's own change)
        level[a] = running
        running += avg(d_min[a])
    peak = max(level.values())

    lab = lambda y: f"{y}-{str(y + 1)[-2:]}"
    f = lambda v, w=7: f"{'':>{w}}" if v is None else f"{v:>+{w}.2f}"
    print(f"Age curve from back-to-back seasons, {lab(years[0])} to {lab(last)} ({args.min_games}+ games in both seasons)")
    print(f"early = pairs starting {lab(start_years[0])} to {lab(split - 1)}; late = pairs starting {lab(split)} to {lab(start_years[-1])}\n")
    print(f"{'age':>4}{'pairs':>7}{'min/g':>8}{'early':>8}{'late':>8}{'fp/g':>8}{'gone':>7}{'vs peak':>9}")
    out = []
    for a in ages:
        k = len(d_min[a])
        g = 100.0 * gone[a] / seen[a] if seen[a] else 0.0
        print(f"{a:>4}{k:>6}{'*' if k < args.min_pairs else ' '}{f(avg(d_min[a]), 8)}{f(avg(early[a]), 8)}{f(avg(late[a]), 8)}{f(avg(d_fp[a]), 8)}"
              f"{g:>6.0f}%{level[a] - peak:>9.1f}")
        out.append([a, k, round(avg(d_min[a]), 3), None if not early[a] else round(avg(early[a]), 3), None if not late[a] else round(avg(late[a]), 3),
                    round(avg(d_fp[a]), 3), round(g, 1), round(level[a] - peak, 2)])
    best = max(level, key=level.get)
    print(f"\n* fewer than {args.min_pairs} pairs. Each row is the change from that age to the next age.")
    print(f"Minutes peak at age {best} on this curve. 'gone' = no games at all the following season.")
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["age", "pairs", "d_min_per_game", "d_min_early", "d_min_late", "d_est_fp_per_game", "gone_pct", "minutes_vs_peak"])
            w.writerows(out)
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
