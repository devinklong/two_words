"""Compare OUR technical/flagrant foul counts with the ones Sleeper scores from, and (with --apply) make Sleeper's count win.

Our counts come from the NBA play-by-play (player_technical_flagrant_fouls, -2.00 each in game_fantasy_scores). Sleeper scores
from its own stat provider (Sportradar), and the two can disagree: on 11/2/26 the NBA play-by-play has a technical on Doncic
that Sleeper's stat line does not, so his game was 73.70 here and 75.70 on Sleeper, and roster 5's week 3 total was off by
2.00. On 11/9/26 that was true of 5 games out of 1,783 compared, always a technical the NBA has and Sleeper does not.

The fantasy score has to equal Sleeper's, so where they differ Sleeper's count is the one that scores:
  --apply   writes Sleeper's count into player_technical_flagrant_fouls and keeps the NBA's count in sleeper_foul_overrides
            (so nothing is lost and every change is listed). It heals itself: if the foul scan writes the NBA count back, the
            next run re-applies it; if Sleeper later agrees with the NBA, the NBA count is restored and the override removed.
Without --apply it only reports and changes nothing.

Checks every player who has a foul in our table this season plus every player on a roster in the current league, one request
per player to Sleeper's public stats endpoint. Games played today are skipped (Sleeper's line may not be complete yet).

Usage (from ~/projects/two_words):  python scripts/sleeper/compare_fouls_with_sleeper.py [--season-id 22026] [--apply] [--all-players]
  --all-players   also check every player with a box score this season (several hundred requests; slower)
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parents[1]))

URL = "https://api.sleeper.com/stats/nba/player/{pid}?season_type=regular&season={season}&grouping=week"
OVERRIDES_DDL = """CREATE TABLE IF NOT EXISTS sleeper_foul_overrides (
    player_id integer NOT NULL, game_id text NOT NULL,
    nba_technical integer NOT NULL, nba_flagrant integer NOT NULL,
    sleeper_technical integer NOT NULL, sleeper_flagrant integer NOT NULL,
    first_seen timestamptz NOT NULL DEFAULT now(), last_checked timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, game_id))"""


def sleeper_games(pid, season):
    """-> {game date (text): (technicals, flagrants)} for the games Sleeper has a stat line for."""
    req = Request(URL.format(pid=pid, season=season), headers={"User-Agent": "two_words-foul-check"})
    with urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    out = {}
    for games in (data or {}).values():
        for g in games if isinstance(games, list) else [games]:
            stats = (g or {}).get("stats") or {}
            if not stats or stats.get("pts") is None and stats.get("min") is None:
                continue   # a line with no stats is a game he did not play
            out[g["date"]] = (int(stats.get("tf") or 0), int(stats.get("ff") or 0))
    return out


def decide(current, override, theirs):
    """One game. current = counts in our table now; override = (nba counts, sleeper counts) stored earlier, or None;
    theirs = Sleeper's counts now. -> (nba counts, action) with action 'none', 'apply' (Sleeper's count should score) or
    'restore' (Sleeper agrees with the NBA again: put the NBA count back and drop the override)."""
    nba = override[0] if override and current == override[1] else current   # an override in effect hides the NBA count
    if nba != theirs:
        return nba, "apply"
    return nba, ("restore" if override else "none")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--all-players", action="store_true")
    args = ap.parse_args()
    season = args.season_id[1:]
    today = date.today().isoformat()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]
    cur.execute("SELECT to_regclass('sleeper_foul_overrides')")
    has_table = cur.fetchone()[0] is not None
    if args.apply and not has_table:
        cur.execute(OVERRIDES_DDL)
        has_table = True
    overrides = {}
    if has_table:
        cur.execute("SELECT player_id, game_id, nba_technical, nba_flagrant, sleeper_technical, sleeper_flagrant FROM sleeper_foul_overrides")
        overrides = {(p, g): ((nt, nf), (st, sf)) for p, g, nt, nf, st, sf in cur.fetchall()}

    # our counts per (player, game date); a game with no row in the fouls table counts as 0 / 0
    cur.execute("""SELECT g.player_id, g.game_date, g.game_id, COALESCE(t.technical_fouls, 0), COALESCE(t.flagrant_fouls, 0), t.player_id IS NOT NULL
                   FROM game_logs g LEFT JOIN player_technical_flagrant_fouls t ON t.player_id = g.player_id AND t.game_id = g.game_id
                   WHERE g.season_id = %s""", (args.season_id,))
    ours = {}
    for pid, gd, gid, tf, ff, has_row in cur.fetchall():
        ours.setdefault(pid, {})[gd.isoformat()] = (gid, (int(tf), int(ff)), has_row)

    cur.execute("""SELECT DISTINCT t.player_id FROM player_technical_flagrant_fouls t JOIN game_logs g ON g.player_id = t.player_id AND g.game_id = t.game_id
                   WHERE g.season_id = %s""", (args.season_id,))
    wanted = {r[0] for r in cur.fetchall()} | {p for p, _g in overrides}
    cur.execute("SELECT DISTINCT nba_player_id FROM roster_ownership WHERE league_id = %s AND nba_player_id IS NOT NULL", (league,))
    wanted |= {r[0] for r in cur.fetchall()}
    if args.all_players:
        wanted |= set(ours)

    cur.execute("SELECT nba_player_id, sleeper_player_id FROM sleeper_player_crosswalk WHERE nba_player_id IS NOT NULL")
    sleeper_id = {n: s for n, s in cur.fetchall()}
    cur.execute("SELECT player_id, full_name FROM nba_players")
    name = dict(cur.fetchall())
    cur.execute("SELECT nba_player_id, game_id, week_number, roster_id, slot FROM nba_lock_decisions WHERE league_id = %s AND season_id = %s", (league, args.season_id))
    locks = {(n, g): (w, r, s) for n, g, w, r, s in cur.fetchall()}

    todo = sorted(p for p in wanted if p in ours)
    no_id = [name.get(p, p) for p in todo if sleeper_id.get(p) is None]
    fetch = [p for p in todo if sleeper_id.get(p) is not None]

    def get(pid):
        try:
            return pid, sleeper_games(sleeper_id[pid], season), None
        except Exception as e:
            return pid, None, str(e)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(get, fetch))

    diffs, restores, failed, games_compared = [], [], [], 0
    for pid, theirs, err in results:
        if err:
            failed.append(f"{name.get(pid, pid)} ({err})")
            continue
        for gd, (gid, current, has_row) in ours[pid].items():
            if gd not in theirs or gd >= today:
                continue
            games_compared += 1
            nba, action = decide(current, overrides.get((pid, gid)), theirs[gd])
            if action == "apply":
                diffs.append((gd, pid, gid, nba, theirs[gd], current, has_row))
            elif action == "restore":
                restores.append((gd, pid, gid, nba, current))

    print(f"season {args.season_id}: {len(fetch)} players, {games_compared} games compared with Sleeper's stat lines\n")
    if diffs:
        print(f"{'date':<11}{'player':<26}{'game':<12}{'NBA T/F':<10}{'Sleeper T/F':<13}{'score change':<14}{'state':<12}locked?")
        for gd, pid, gid, nba, theirs, current, has_row in sorted(diffs):
            lk = locks.get((pid, gid))
            state = "in effect" if current == theirs else "to apply"
            print(f"{gd:<11}{str(name.get(pid, pid))[:25]:<26}{gid:<12}{f'{nba[0]}/{nba[1]}':<10}{f'{theirs[0]}/{theirs[1]}':<13}"
                  f"{f'{2.0 * (sum(nba) - sum(theirs)):+.2f}':<14}{state:<12}{f'wk {lk[0]} roster {lk[1]} {lk[2]}' if lk else ''}")
    for gd, pid, gid, nba, current in sorted(restores):
        print(f"{gd:<11}{str(name.get(pid, pid))[:25]:<26}{gid:<12}Sleeper now agrees with the NBA ({nba[0]}/{nba[1]}); override no longer needed")
    pending = [d for d in diffs if d[5] != d[4]]
    print(f"\n{len(diffs)} game(s) where Sleeper's foul count differs from the NBA's: {len(diffs) - len(pending)} already scored Sleeper's way, {len(pending)} to apply"
          + (f"; {len(restores)} to restore" if restores else ""))
    if no_id:
        print(f"no Sleeper id for {len(no_id)} player(s), not checked: {', '.join(map(str, no_id[:12]))}{' ...' if len(no_id) > 12 else ''}")
    if failed:
        print(f"request failed for {len(failed)} player(s): {'; '.join(failed[:5])}{' ...' if len(failed) > 5 else ''}")

    if not args.apply:
        if pending or restores:
            print("nothing written (run with --apply to make Sleeper's count the one that scores)")
        cur.close(); conn.close()
        return

    for gd, pid, gid, nba, theirs, current, has_row in diffs:
        if current != theirs:
            if has_row:
                cur.execute("UPDATE player_technical_flagrant_fouls SET technical_fouls = %s, flagrant_fouls = %s WHERE player_id = %s AND game_id = %s",
                            (theirs[0], theirs[1], pid, gid))
            else:   # Sleeper has a foul the NBA play-by-play scan did not find
                cur.execute("INSERT INTO player_technical_flagrant_fouls (player_id, game_id, technical_fouls, flagrant_fouls) VALUES (%s, %s, %s, %s)",
                            (pid, gid, theirs[0], theirs[1]))
        cur.execute("""INSERT INTO sleeper_foul_overrides (player_id, game_id, nba_technical, nba_flagrant, sleeper_technical, sleeper_flagrant)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON CONFLICT (player_id, game_id) DO UPDATE SET nba_technical = EXCLUDED.nba_technical, nba_flagrant = EXCLUDED.nba_flagrant,
                           sleeper_technical = EXCLUDED.sleeper_technical, sleeper_flagrant = EXCLUDED.sleeper_flagrant, last_checked = now()""",
                    (pid, gid, nba[0], nba[1], theirs[0], theirs[1]))
    for gd, pid, gid, nba, current in restores:
        if current != nba:
            cur.execute("UPDATE player_technical_flagrant_fouls SET technical_fouls = %s, flagrant_fouls = %s WHERE player_id = %s AND game_id = %s",
                        (nba[0], nba[1], pid, gid))
        cur.execute("DELETE FROM sleeper_foul_overrides WHERE player_id = %s AND game_id = %s", (pid, gid))
    conn.commit()
    print(f"applied: {len(pending)} foul count(s) set to Sleeper's, {len(restores)} restored; {len(diffs)} override(s) on record in sleeper_foul_overrides")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
