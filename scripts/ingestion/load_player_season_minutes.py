"""Load one row per player per season (games, minutes, season totals, age) into nba_player_season_minutes, 2013-14 onward.

This is the DISTILLED history for playing-time and age work: about 500-600 rows a season, one request per season to the
NBA's LeagueDashPlayerStats. It is separate from game_logs on purpose. Nothing in the lock pipeline reads it, and for the
seasons that game_logs covers game by game (2021-22 on) the exact per-game scores stay the source of truth.

est_fantasy_points applies this league's Sleeper scoring to the season totals: every per-stat value plus +3 per double-double
and +5 per triple-double. It is an ESTIMATE: the 40- and 50-point, 15-assist and 20-rebound game bonuses and the -2 for
technical and flagrant fouls cannot be rebuilt from season totals. Use it to compare seasons with each other, never against
the exact scores in game_fantasy_scores.

Additive and safe to re-run (rows are upserted). The season in progress is included and simply grows.

Usage (from ~/projects/two_words):  python scripts/ingestion/load_player_season_minutes.py [--from-season 22013] [--to-season 22026] [--dry-run]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

DDL = """CREATE TABLE IF NOT EXISTS nba_player_season_minutes (
    player_id          integer NOT NULL,
    season_id          text    NOT NULL,
    player_name        text,
    team_abbreviation  text,
    age                numeric,
    games              integer NOT NULL,
    minutes            numeric NOT NULL,
    pts integer, oreb integer, dreb integer, ast integer, stl integer, blk integer, tov integer,
    fgm integer, fga integer, fg3m integer, fg3a integer, ftm integer, fta integer, pf integer,
    plus_minus         integer,
    double_doubles     integer,
    triple_doubles     integer,
    est_fantasy_points numeric,
    loaded_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, season_id))"""
STATS = ["PTS", "OREB", "DREB", "AST", "STL", "BLK", "TOV", "FGM", "FGA", "FG3M", "FG3A", "FTM", "FTA", "PF", "PLUS_MINUS", "DD2", "TD3"]


def season_label(season_id):
    """'22024' -> '2024-25'"""
    start = int(str(season_id)[1:])
    return f"{start}-{str(start + 1)[-2:]}"


def n(v):
    """A whole number; blanks and NaN count as 0."""
    try:
        return 0 if v is None or v != v else int(round(float(v)))
    except (TypeError, ValueError):
        return 0


def estimate_fantasy(r):
    """This league's scoring on season totals (see the module note for what it leaves out). Same values as game_fantasy_scores."""
    reb = n(r["OREB"]) + n(r["DREB"])
    return round(0.5 * n(r["PTS"]) + 1.5 * reb + 0.5 * n(r["OREB"]) + 2 * n(r["AST"]) + 3 * n(r["STL"]) + 3 * n(r["BLK"]) - n(r["TOV"])
                 + n(r["FGM"]) - 0.45 * n(r["FGA"]) + n(r["FTM"]) - 0.5 * n(r["FTA"]) + 0.5 * n(r["FG3M"])
                 + 3 * n(r["DD2"]) + 5 * n(r["TD3"]), 2)


def fetch(season_id, tries=3):
    from nba_api.stats.endpoints import leaguedashplayerstats
    last = None
    for attempt in range(tries):
        try:
            return leaguedashplayerstats.LeagueDashPlayerStats(
                season=season_label(season_id), season_type_all_star="Regular Season", per_mode_detailed="Totals", timeout=60
            ).get_data_frames()[0]
        except Exception as e:   # the NBA's stats site drops requests now and then
            last = e
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"season {season_id}: the NBA request failed {tries} times ({last})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-season", default="22013")
    ap.add_argument("--to-season", help="default: the season in current_season_config")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    if not args.to_season:
        cur.execute("SELECT season_id FROM current_season_config WHERE id = 1")
        args.to_season = cur.fetchone()[0]
    if not args.dry_run:
        cur.execute(DDL)
    seasons = [f"2{y}" for y in range(int(args.from_season[1:]), int(args.to_season[1:]) + 1)]

    for sid in seasons:
        rows = []
        for r in fetch(sid).to_dict("records"):
            games = n(r.get("GP"))
            if games <= 0:
                continue
            age = r.get("AGE")
            rows.append((int(r["PLAYER_ID"]), sid, r.get("PLAYER_NAME"), r.get("TEAM_ABBREVIATION"),
                         None if age is None or age != age else float(age), games, round(float(r.get("MIN") or 0), 1),
                         *[n(r.get(k)) for k in STATS], estimate_fantasy(r)))
        if not args.dry_run:
            cur.executemany("""INSERT INTO nba_player_season_minutes (player_id, season_id, player_name, team_abbreviation, age, games, minutes,
                                   pts, oreb, dreb, ast, stl, blk, tov, fgm, fga, fg3m, fg3a, ftm, fta, pf, plus_minus,
                                   double_doubles, triple_doubles, est_fantasy_points)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (player_id, season_id) DO UPDATE SET player_name = EXCLUDED.player_name,
                                   team_abbreviation = EXCLUDED.team_abbreviation, age = EXCLUDED.age, games = EXCLUDED.games, minutes = EXCLUDED.minutes,
                                   pts = EXCLUDED.pts, oreb = EXCLUDED.oreb, dreb = EXCLUDED.dreb, ast = EXCLUDED.ast, stl = EXCLUDED.stl,
                                   blk = EXCLUDED.blk, tov = EXCLUDED.tov, fgm = EXCLUDED.fgm, fga = EXCLUDED.fga, fg3m = EXCLUDED.fg3m,
                                   fg3a = EXCLUDED.fg3a, ftm = EXCLUDED.ftm, fta = EXCLUDED.fta, pf = EXCLUDED.pf, plus_minus = EXCLUDED.plus_minus,
                                   double_doubles = EXCLUDED.double_doubles, triple_doubles = EXCLUDED.triple_doubles,
                                   est_fantasy_points = EXCLUDED.est_fantasy_points, loaded_at = now()""", rows)
            conn.commit()
        total_min = sum(x[6] for x in rows)
        print(f"season {sid} ({season_label(sid)}): {len(rows)} players, {sum(x[5] for x in rows):,} player-games, {total_min:,.0f} minutes"
              + (" (dry run, nothing written)" if args.dry_run else ""))
        time.sleep(1.5)

    if not args.dry_run:
        # cross-check against the game-by-game data where both exist: games and minutes should agree closely
        cur.execute("""SELECT m.season_id, sum(m.games), sum(g.games), round(sum(m.minutes)), sum(g.minutes)
                       FROM nba_player_season_minutes m
                       JOIN (SELECT season_id, player_id, count(*) AS games, sum(minutes) AS minutes FROM game_logs
                             WHERE season_id LIKE '2%%' GROUP BY 1, 2) g USING (season_id, player_id)
                       WHERE m.season_id = ANY(%s) GROUP BY 1 ORDER BY 1""", (seasons,))
        rows = cur.fetchall()
        if rows:
            print("\ncheck against game_logs (same players, seasons in both):")
            for sid, mg, gg, mm, gm in rows:
                print(f"  {sid}: games {mg:,} here vs {gg:,} in game_logs; minutes {float(mm):,.0f} here vs {float(gm):,.0f} in game_logs")
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
