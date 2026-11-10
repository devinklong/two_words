"""Average minutes per game by player age, for every season in the database. Read-only.

Needs nba_player_season_bio (python scripts/ingestion/load_player_season_bio.py). Age is the NBA's whole-number age for the
player in that season. Each cell is total minutes divided by total games for every player of that age, so a player who
appears in 70 games counts 70 times and one who appears in 5 counts 5 times. Only games a player actually appeared in are in
game_logs, so a missed game is not a 0.

Reading it: this is who is ON THE FLOOR at each age, not how one player's minutes change as he ages. Old players still in
the league are the good ones (the rest are gone), so the late-30s rows are not a forecast for an average player.

Usage (from ~/projects/two_words):  python scripts/minutes_by_age.py [--min-games 1] [--min-players 5] [--csv out.csv]
  --min-games N     only player-seasons with at least N games (e.g. 20 to leave out short stints)
  --min-players N   cells built on fewer than N players are shown with a * (default 5)
"""
import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-games", type=int, default=1)
    ap.add_argument("--min-players", type=int, default=5)
    ap.add_argument("--csv")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SET TRANSACTION READ ONLY")
    cur.execute("SELECT to_regclass('nba_player_season_bio')")
    if cur.fetchone()[0] is None:
        raise SystemExit("nba_player_season_bio is missing: run  python scripts/ingestion/load_player_season_bio.py  first")
    cur.execute("""
        WITH ps AS (
            SELECT g.season_id, g.player_id, count(*) AS games, sum(g.minutes) AS minutes
            FROM game_logs g WHERE g.season_id LIKE '2%%' AND g.minutes IS NOT NULL
            GROUP BY 1, 2 HAVING count(*) >= %s)
        SELECT ps.season_id, floor(b.age)::int AS age, count(*) AS players, sum(ps.games) AS games, sum(ps.minutes) AS minutes
        FROM ps JOIN nba_player_season_bio b ON b.player_id = ps.player_id AND b.season_id = ps.season_id
        WHERE b.age IS NOT NULL GROUP BY 1, 2 ORDER BY 2, 1""", (args.min_games,))
    cell = {}
    for sid, age, players, games, minutes in cur.fetchall():
        cell[(age, sid)] = (int(players), int(games), float(minutes))
    cur.execute("""SELECT count(*) FROM (SELECT g.season_id, g.player_id FROM game_logs g
                       LEFT JOIN nba_player_season_bio b ON b.player_id = g.player_id AND b.season_id = g.season_id
                       WHERE g.season_id LIKE '2%%' AND b.age IS NULL GROUP BY 1, 2) x""")
    no_age = cur.fetchone()[0]
    cur.close(); conn.close()
    if not cell:
        raise SystemExit("no rows: is nba_player_season_bio loaded for the seasons in game_logs?")

    seasons = sorted({s for _a, s in cell})
    ages = sorted({a for a, _s in cell})
    label = lambda s: f"{s[1:]}-{str(int(s[1:]) + 1)[-2:]}"
    total = defaultdict(lambda: [0, 0, 0.0])
    for (age, _sid), (p, g, m) in cell.items():
        t = total[age]; t[0] += p; t[1] += g; t[2] += m

    def fmt(v):
        if v is None:
            return f"{'':>9}"
        p, g, m = v
        return f"{m / g:>8.1f}{'*' if p < args.min_players else ' '}"

    print(f"Average minutes per game by age" + (f" (player-seasons with {args.min_games}+ games)" if args.min_games > 1 else ""))
    print(f"{'age':>4} " + "".join(f"{label(s):>9}" for s in seasons) + f"{'all':>9}{'players':>9}")
    for age in ages:
        print(f"{age:>4} " + "".join(fmt(cell.get((age, s))) for s in seasons) + fmt(tuple(total[age])) + f"{total[age][0]:>8}")
    print(f"\n* fewer than {args.min_players} players. 'players' counts player-seasons across all seasons shown."
          + (f" The current season ({label(seasons[-1])}) is in progress." if seasons else ""))
    if no_age:
        print(f"{no_age} player-season(s) in game_logs have no age and are left out.")

    print(f"\nPlayers at each age")
    print(f"{'age':>4} " + "".join(f"{label(s):>9}" for s in seasons))
    for age in ages:
        print(f"{age:>4} " + "".join(f"{(cell.get((age, s)) or ('',))[0]:>9}" for s in seasons))

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["age", "season_id", "players", "games", "avg_minutes_per_game"])
            for (age, sid), (p, g, m) in sorted(cell.items()):
                w.writerow([age, sid, p, g, round(m / g, 2)])
        print(f"\nwrote {args.csv}")


if __name__ == "__main__":
    main()
