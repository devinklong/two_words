"""Load each player's age (and height, weight, draft slot, country) for every season in game_logs into nba_player_season_bio.

One request per SEASON to the NBA's LeagueDashPlayerBioStats (6 requests for 2021-22 through 2026-27), not one per player.
AGE is the NBA's own figure for that player in that season, a whole number; for the season in progress it is his age now.
There is no birth date in this endpoint, so ages are per season, not per game.

Additive and safe to re-run: rows are upserted, nothing else is touched. Used by scripts/minutes_by_age.py and meant as the
first piece of the dynasty-value work (age curves).

Usage (from ~/projects/two_words):  python scripts/ingestion/load_player_season_bio.py [--season-id 22026] [--dry-run]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

DDL = """CREATE TABLE IF NOT EXISTS nba_player_season_bio (
    player_id      integer NOT NULL,
    season_id      text    NOT NULL,
    player_name    text,
    age            numeric,
    height_inches  numeric,
    weight_lbs     numeric,
    draft_year     integer,
    draft_round    integer,
    draft_number   integer,
    country        text,
    college        text,
    loaded_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, season_id))"""


def season_label(season_id):
    """'22024' -> '2024-25'"""
    start = int(str(season_id)[1:])
    return f"{start}-{str(start + 1)[-2:]}"


def num(v):
    """A number, or None for blanks and words like 'Undrafted'."""
    try:
        if v is None or v != v:   # None or NaN
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(season_id, tries=3):
    from nba_api.stats.endpoints import leaguedashplayerbiostats
    last = None
    for attempt in range(tries):
        try:
            df = leaguedashplayerbiostats.LeagueDashPlayerBioStats(
                season=season_label(season_id), season_type_all_star="Regular Season", per_mode_simple="Totals", timeout=60
            ).get_data_frames()[0]
            return df
        except Exception as e:   # the NBA's stats site drops requests now and then
            last = e
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"season {season_id}: the NBA request failed {tries} times ({last})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", help="one season, e.g. 22026 (default: every season in game_logs)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT season_id, count(DISTINCT player_id) FROM game_logs WHERE season_id LIKE '2%%' GROUP BY 1 ORDER BY 1")
    in_logs = dict(cur.fetchall())
    seasons = [args.season_id] if args.season_id else list(in_logs)
    if not args.dry_run:
        cur.execute(DDL)

    for sid in seasons:
        df = fetch(sid)
        rows = []
        for r in df.to_dict("records"):
            as_int = lambda v: int(num(v)) if num(v) is not None else None
            rows.append((int(r["PLAYER_ID"]), str(sid), r.get("PLAYER_NAME"), num(r.get("AGE")), num(r.get("PLAYER_HEIGHT_INCHES")),
                         num(r.get("PLAYER_WEIGHT")), as_int(r.get("DRAFT_YEAR")), as_int(r.get("DRAFT_ROUND")), as_int(r.get("DRAFT_NUMBER")),
                         r.get("COUNTRY") or None, r.get("COLLEGE") or None))
        no_age = sum(1 for x in rows if x[3] is None)
        if not args.dry_run:
            cur.executemany("""INSERT INTO nba_player_season_bio (player_id, season_id, player_name, age, height_inches, weight_lbs,
                                   draft_year, draft_round, draft_number, country, college)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (player_id, season_id) DO UPDATE SET player_name = EXCLUDED.player_name, age = EXCLUDED.age,
                                   height_inches = EXCLUDED.height_inches, weight_lbs = EXCLUDED.weight_lbs, draft_year = EXCLUDED.draft_year,
                                   draft_round = EXCLUDED.draft_round, draft_number = EXCLUDED.draft_number, country = EXCLUDED.country,
                                   college = EXCLUDED.college, loaded_at = now()""", rows)
            conn.commit()
        print(f"season {sid} ({season_label(sid)}): {len(rows)} players from the NBA, {in_logs.get(sid, 0)} in game_logs"
              + (f", {no_age} without an age" if no_age else "") + (" (dry run, nothing written)" if args.dry_run else ""))
        time.sleep(1.5)

    if not args.dry_run:
        cur.execute("""SELECT g.season_id, count(DISTINCT g.player_id) FROM game_logs g
                       LEFT JOIN nba_player_season_bio b ON b.player_id = g.player_id AND b.season_id = g.season_id
                       WHERE g.season_id = ANY(%s) AND b.age IS NULL GROUP BY 1 ORDER BY 1""", ([str(s) for s in seasons],))
        missing = cur.fetchall()
        print("every player in game_logs for those seasons has an age" if not missing
              else "players in game_logs with no age: " + ", ".join(f"{s}: {n}" for s, n in missing))
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
