"""Compare OUR technical/flagrant foul counts with the ones Sleeper scores from. Read-only.

Our counts come from the NBA play-by-play (player_technical_flagrant_fouls, -2.00 each in game_fantasy_scores). Sleeper scores
from its own stat provider (Sportradar), and the two can disagree: on 11/2/26 the NBA play-by-play has a technical on Doncic
that Sleeper's stat line does not, so his game is 73.70 here and 75.70 on Sleeper, and roster 5's week 3 total is off by 2.00.

Checks every player who has a foul in our table this season plus every player on a roster in the current league, one request
per player to Sleeper's public stats endpoint. For each game both sides know, prints the games where the counts differ, the
size of the score difference (ours minus Sleeper's), and whether that game is a stored lock.

Usage (from ~/projects/two_words):  python scripts/sleeper/compare_fouls_with_sleeper.py [--season-id 22026] [--all-players]
  --all-players   also check every player with a box score this season (several hundred requests; slower)
"""
import argparse
import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.append(str(Path(__file__).resolve().parents[1]))

URL = "https://api.sleeper.com/stats/nba/player/{pid}?season_type=regular&season={season}&grouping=week"


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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", default="22026")
    ap.add_argument("--all-players", action="store_true")
    args = ap.parse_args()
    season = args.season_id[1:]

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SELECT league_id FROM sleeper_current_league")
    league = cur.fetchone()[0]

    # our counts per (player, game date); a game with no row in the fouls table counts as 0 / 0
    cur.execute("""SELECT g.player_id, g.game_date, g.game_id, COALESCE(t.technical_fouls, 0), COALESCE(t.flagrant_fouls, 0)
                   FROM game_logs g LEFT JOIN player_technical_flagrant_fouls t ON t.player_id = g.player_id AND t.game_id = g.game_id
                   WHERE g.season_id = %s""", (args.season_id,))
    ours = {}
    for pid, gd, gid, tf, ff in cur.fetchall():
        ours.setdefault(pid, {})[gd.isoformat()] = (gid, int(tf), int(ff))

    cur.execute("""SELECT DISTINCT t.player_id FROM player_technical_flagrant_fouls t JOIN game_logs g ON g.player_id = t.player_id AND g.game_id = t.game_id
                   WHERE g.season_id = %s""", (args.season_id,))
    wanted = {r[0] for r in cur.fetchall()}
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
    cur.close(); conn.close()

    diffs, no_id, failed, games_compared = [], [], [], 0
    todo = sorted(wanted)
    for i, pid in enumerate(todo, 1):
        sid = sleeper_id.get(pid)
        if sid is None:
            no_id.append(name.get(pid, pid))
            continue
        try:
            theirs = sleeper_games(sid, season)
        except Exception as e:
            failed.append(f"{name.get(pid, pid)} ({e})")
            continue
        for gd, (gid, tf, ff) in (ours.get(pid) or {}).items():
            if gd not in theirs:
                continue
            games_compared += 1
            stf, sff = theirs[gd]
            if (tf, ff) != (stf, sff):
                diffs.append((gd, name.get(pid, pid), gid, tf, ff, stf, sff, -2.0 * ((tf + ff) - (stf + sff)), locks.get((pid, gid))))
        if i % 25 == 0:
            print(f"  ... {i}/{len(todo)} players checked", file=sys.stderr)
        time.sleep(0.15)

    print(f"season {args.season_id}: {len(todo)} players, {games_compared} games compared with Sleeper's stat lines\n")
    if diffs:
        print(f"{'date':<11}{'player':<26}{'game':<12}{'ours T/F':<10}{'Sleeper T/F':<13}{'our score is':<14}locked?")
        for gd, nm, gid, tf, ff, stf, sff, delta, lk in sorted(diffs):
            where = f"wk {lk[0]} roster {lk[1]} {lk[2]}" if lk else ""
            print(f"{gd:<11}{str(nm)[:25]:<26}{gid:<12}{f'{tf}/{ff}':<10}{f'{stf}/{sff}':<13}{f'{delta:+.2f}':<14}{where}")
    print(f"\n{len(diffs)} game(s) where the foul counts differ" + (f"; {sum(1 for d in diffs if d[8])} of them are stored locks" if diffs else ""))
    if no_id:
        print(f"no Sleeper id for {len(no_id)} player(s), not checked: {', '.join(map(str, no_id[:12]))}{' ...' if len(no_id) > 12 else ''}")
    if failed:
        print(f"request failed for {len(failed)} player(s): {'; '.join(failed[:5])}{' ...' if len(failed) > 5 else ''}")


if __name__ == "__main__":
    main()
