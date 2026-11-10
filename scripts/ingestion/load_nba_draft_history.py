"""Load the NBA draft (every pick, both rounds) for each class into nba_draft_history, 2014 onward.

Second piece of the pick-value work (scripts/pick_value_proxy.py). The bio table only has players who played in 2021 or later, so a
first-rounder who washed out early is missing from it; the draft itself lists everybody, which lets a pick that never played count
as the miss it is. One request per class to the NBA's DraftHistory.

Additive and safe to re-run: rows are upserted by (draft_year, overall_pick).

Usage (from ~/projects/two_words):  python scripts/ingestion/load_nba_draft_history.py [--from-year 2014] [--to-year 2026] [--dry-run]
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

DDL = """CREATE TABLE IF NOT EXISTS nba_draft_history (
    draft_year        integer NOT NULL,
    overall_pick      integer NOT NULL,
    round_number      integer,
    round_pick        integer,
    player_id         integer,
    player_name       text,
    team_abbreviation text,
    loaded_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_year, overall_pick))"""


def fetch(year, tries=3):
    from nba_api.stats.endpoints import drafthistory
    last = None
    for attempt in range(tries):
        try:
            return drafthistory.DraftHistory(league_id="00", season_year_nullable=str(year), timeout=60).get_data_frames()[0]
        except Exception as e:   # the NBA's stats site drops requests now and then
            last = e
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"draft {year}: the NBA request failed {tries} times ({last})")


def to_int(v):
    try:
        return None if v is None or v != v else int(v)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-year", type=int, default=2014)
    ap.add_argument("--to-year", type=int, help="default: the starting year of the season in current_season_config")
    ap.add_argument("--dry-run", action="store_true", help="fetch and report, write nothing")
    args = ap.parse_args()

    from db_connection import get_connection
    conn = get_connection()
    cur = conn.cursor()
    if args.to_year is None:
        cur.execute("SELECT season_id FROM current_season_config WHERE id = 1")
        args.to_year = int(cur.fetchone()[0][1:])
    if not args.dry_run:
        cur.execute(DDL)

    for year in range(args.from_year, args.to_year + 1):
        rows = []
        for r in fetch(year).to_dict("records"):
            pick = to_int(r.get("OVERALL_PICK"))
            if pick is None:
                continue
            rows.append((year, pick, to_int(r.get("ROUND_NUMBER")), to_int(r.get("ROUND_PICK")), to_int(r.get("PERSON_ID")),
                         r.get("PLAYER_NAME"), r.get("TEAM_ABBREVIATION")))
        firsts = sum(1 for x in rows if x[1] <= 30)
        print(f"{year}: {len(rows)} picks ({firsts} of the first 30)" + (" (dry run, nothing written)" if args.dry_run else ""))
        if not args.dry_run and rows:
            cur.executemany("""INSERT INTO nba_draft_history (draft_year, overall_pick, round_number, round_pick, player_id, player_name,
                                   team_abbreviation)
                               VALUES (%s,%s,%s,%s,%s,%s,%s)
                               ON CONFLICT (draft_year, overall_pick) DO UPDATE SET round_number = EXCLUDED.round_number,
                                   round_pick = EXCLUDED.round_pick, player_id = EXCLUDED.player_id, player_name = EXCLUDED.player_name,
                                   team_abbreviation = EXCLUDED.team_abbreviation, loaded_at = now()""", rows)
            conn.commit()
        time.sleep(1.5)
    cur.close(); conn.close()


if __name__ == "__main__":
    main()
