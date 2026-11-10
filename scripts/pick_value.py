"""What did each rookie-draft slot turn into, measured against what the waiver wire hands out for free? Read-only.

Needs sleeper_draft_picks (python scripts/sleeper/ingest_drafts.py) and nba_player_season_bio (draft year, to tell a rookie draft from a
startup draft). For every pick it follows the player through his first three seasons and compares his fantasy points per game with
the REPLACEMENT LINE of that season: the average of the players ranked just below the starting pool (default ranks 91-110: 10 teams x
9 starters = 90 starters, then the next 20 players, roughly who a manager can pick up). Like the lock bar, a pick is worth only what it
adds ABOVE that line. A rookie who never plays, or plays below the line, adds 0.

  vor    points per game above the line (0 if below it); averaged over a band of picks
  clear  share of the band's picks above the line in that season (a pick that never played counts as a miss)

The line is built from ranks, not from the real unrostered pool, because rosters are only kept for the current week. It is a
yardstick for comparing slots with each other, not an exact price: move it with --line-start / --line-end and watch whether the
conclusion changes. Exact scores (game_fantasy_scores) from 2021-22 on, so the 2026-27 season is partial and marked.

Usage (from ~/projects/two_words):  python scripts/pick_value.py [--line-start 91] [--line-end 110] [--min-games 20] [--picks]
  --picks   also list every pick
"""
import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

YEARS = (1, 2, 3)


def replacement_line(fpg_by_player, start, end):
    """fpg_by_player = {player_id: points per game} for the players with enough games. -> (line, ranks {player_id: rank})."""
    ordered = sorted(fpg_by_player.items(), key=lambda kv: -kv[1])
    ranks = {pid: i + 1 for i, (pid, _v) in enumerate(ordered)}
    window = [v for pid, v in ordered if start <= ranks[pid] <= end]
    return (sum(window) / len(window) if window else None), ranks


def band(pick_in_round, teams, rnd):
    """'R1 early/mid/late' by thirds of the round; later rounds are one band each."""
    if rnd >= 3:
        return "R3+"
    third = (pick_in_round - 1) * 3 // max(teams, 1)
    return f"R{rnd} {('early', 'mid', 'late')[min(third, 2)]}"


def vor(fpg, line):
    return max(0.0, fpg - line) if fpg is not None and line is not None else 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--line-start", type=int, default=91)
    ap.add_argument("--line-end", type=int, default=110)
    ap.add_argument("--min-games", type=int, default=20, help="games for a player to be ranked in a finished season (default 20)")
    ap.add_argument("--picks", action="store_true")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    for t in ("sleeper_draft_picks", "nba_player_season_bio"):
        cur.execute("SELECT to_regclass(%s)", (t,))
        if cur.fetchone()[0] is None:
            raise SystemExit(f"{t} is missing: run scripts/sleeper/ingest_drafts.py and scripts/ingestion/load_player_season_bio.py first")
    cur.execute("SELECT season_id FROM current_season_config WHERE id = 1")
    current = cur.fetchone()[0]

    cur.execute("""SELECT d.draft_id, d.season, d.teams, p.round, p.pick_no, p.nba_player_id, p.player_name,
                          (SELECT max(b.draft_year) FROM nba_player_season_bio b WHERE b.player_id = p.nba_player_id)
                   FROM sleeper_draft_picks p JOIN sleeper_drafts d USING (draft_id) ORDER BY d.season, p.pick_no""")
    picks = cur.fetchall()
    if not picks:
        raise SystemExit("no drafts loaded: run python scripts/sleeper/ingest_drafts.py")

    # a draft is a ROOKIE draft when most of its identified players were drafted by the NBA that same year
    by_draft = defaultdict(list)
    for r in picks:
        by_draft[(r[0], r[1])].append(r)
    rookie_drafts = set()
    for (did, season), rows in by_draft.items():
        known = [x for x in rows if x[7] is not None]
        same = sum(1 for x in known if x[7] == int(season))
        kind = "rookie" if known and same / len(known) >= 0.5 else "startup/other"
        print(f"{season} draft {did}: {len(rows)} picks, {same} of {len(known)} identified players were that year's NBA draftees -> {kind} draft")
        if kind == "rookie":
            rookie_drafts.add(did)
    if not rookie_drafts:
        raise SystemExit("no rookie drafts found")

    cur.execute("""SELECT season_id, player_id, count(*), avg(fantasy_score)
                   FROM game_fantasy_scores WHERE season_id >= '22021' GROUP BY 1, 2""")
    stats = defaultdict(dict)   # season_id -> {player: (games, fpg)}
    for sid, pid, g, fpg in cur.fetchall():
        stats[sid][pid] = (int(g), float(fpg))
    cur.close(); conn.close()

    lines, ranks, in_progress = {}, {}, {}
    for sid, players in stats.items():
        floor_games = args.min_games if sid != current else max(3, args.min_games // 3)
        lines[sid], ranks[sid] = replacement_line({p: v[1] for p, v in players.items() if v[0] >= floor_games}, args.line_start, args.line_end)
        in_progress[sid] = sid == current

    rows = []   # (draft_season, band, pick_no, name, [(sid, games, fpg, rank, vor) per year])
    for did, season, teams, rnd, pick_no, pid, name, _dy in picks:
        if did not in rookie_drafts:
            continue
        teams = teams or 10
        years = []
        for k in YEARS:
            sid = f"2{int(season) + k - 1}"
            if sid not in stats:
                years.append(None)
                continue
            g, fpg = stats[sid].get(pid, (0, None)) if pid else (0, None)
            qualifies = fpg is not None and sid in ranks and pid in ranks[sid]
            years.append((sid, g, fpg, ranks[sid].get(pid) if qualifies else None, vor(fpg, lines.get(sid)) if g else 0.0))
        rows.append((season, band((pick_no - 1) % teams + 1, teams, rnd), pick_no, name or "?", years))

    if args.picks:
        print(f"\n{'draft':<6}{'pick':>5}  {'player':<24}" + "".join(f"{'year ' + str(k):>22}" for k in YEARS))
        for season, b, pick_no, name, years in rows:
            cells = []
            for y in years:
                cells.append(f"{'':>22}" if y is None else (f"{'no games':>22}" if not y[1] else
                             f"{y[2]:>8.1f} #{(y[3] or 0):<4} +{y[4]:<5.1f}{'~' if in_progress[y[0]] else ' '}"[:22].rjust(22)))
            print(f"{season:<6}{pick_no:>5}  {name:<24}" + "".join(cells))
        print("   points/g, rank among players with enough games, +points/g above the line; ~ = season in progress")

    print(f"\nReplacement line (average fantasy points/g of ranks {args.line_start}-{args.line_end}): "
          + ", ".join(f"{sid[1:]}: {lines[sid]:.1f}" for sid in sorted(lines) if lines[sid] is not None))
    order = ["R1 early", "R1 mid", "R1 late", "R2 early", "R2 mid", "R2 late", "R3+"]
    print(f"\n{'band':<10}{'picks':>6}" + "".join(f"{'yr' + str(k) + ' clear':>10}{'yr' + str(k) + ' vor':>9}" for k in YEARS))
    for b in order:
        grp = [r for r in rows if r[1] == b]
        if not grp:
            continue
        cells = ""
        for i in range(len(YEARS)):
            ys = [r[4][i] for r in grp if r[4][i] is not None]
            if not ys:
                cells += f"{'':>10}{'':>9}"
                continue
            cells += f"{100.0 * sum(1 for y in ys if y[4] > 0) / len(ys):>9.0f}%{sum(y[4] for y in ys) / len(ys):>9.2f}"
        print(f"{b:<10}{len(grp):>6}{cells}")
    print("\nclear = share above the line (never played = miss). vor = average points/g above the line, 0 when below. A year with no "
          "seasons yet (or still in progress) is thinner than the rest; with two or three drafts, read these as rough bands, not prices.")


if __name__ == "__main__":
    main()
