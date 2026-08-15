"""
Historical backfill for team_game_stats -- loops every distinct
(game_date, game_id) pair already in game_logs (5 seasons), pulls each
date's home/away mapping ONCE via get_scoreboard_games.py (not once per
game -- one ScoreboardV3 call per DATE, one BoxScoreTraditionalV3 call
per GAME, same pattern load_daily_game_logs.py already uses for a single
day, just looped across every historical date instead of just yesterday).

RESUMABLE BY DESIGN -- run this in chunks across the day/week without
worrying about re-doing work:
  - ON CONFLICT DO NOTHING on every insert, same as every other loader
    in this project
  - --season-id filter lets you run one season at a time
  - Skips any date with 0 games needing backfill (already-covered dates
    exit fast without hitting the box score endpoint at all)

ESTIMATED SCALE (see conversation notes, 8/11/26): ~6,150 games across 5
seasons -> ~6,150 BoxScoreTraditionalV3 calls, plus roughly 1 ScoreboardV3
call per distinct game date (~150-180 dates/season). At the same 0.6s
courtesy sleep used elsewhere in this project, that's over an hour of
sleep time alone for a full run -- chunk by season, don't try to run the
whole thing in one sitting.

Run (all seasons):        python scripts/backfill_team_game_stats.py
Run (one season):         python scripts/backfill_team_game_stats.py --season-id 22021
"""

import argparse
import time
import sys

from nba_api.stats.endpoints import boxscoretraditionalv3
from pathlib import Path
from data_cleaning_team_boxscore import clean_team_boxscore
from get_scoreboard_games import get_completed_games_with_home_away
from db_connection import get_connection

sys.path.append(str(Path(__file__).resolve().parents[1]))

SLEEP_SECONDS_BETWEEN_CALLS = 0.6

TEAM_GAME_STATS_COLUMNS = [
    "game_id", "team_id", "opponent_team_id", "season_id", "game_date",
    "is_home", "fgm", "fga", "fg3m", "fg3a", "ftm", "fta",
    "oreb", "dreb", "ast", "stl", "blk", "tov", "pf", "pts", "plus_minus",
]


def fetch_dates_needing_backfill(conn, season_id=None) -> list:
    """
    Distinct game_dates from game_logs not yet fully covered in
    team_game_stats -- a date "needs" backfill if ANY of its game_ids
    are missing from team_game_stats, so a partially-completed date
    (e.g. interrupted mid-run) gets picked back up correctly, not
    skipped as "already done."
    """
    cur = conn.cursor()
    query = """
        SELECT DISTINCT gl.game_date, gl.season_id
        FROM game_logs gl
        WHERE NOT EXISTS (
            SELECT 1 FROM team_game_stats tgs WHERE tgs.game_id = gl.game_id
        )
    """
    params = ()
    if season_id:
        query += " AND gl.season_id = %s"
        params = (season_id,)
    query += " ORDER BY gl.game_date"
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    return [(r[0], r[1]) for r in rows]


def load_team_game_stats(df, conn) -> int:
    if df.empty:
        return 0
    from psycopg2.extras import execute_values
    cur = conn.cursor()
    rows = list(df.itertuples(index=False, name=None))
    execute_values(
        cur,
        f"""
        INSERT INTO team_game_stats ({", ".join(TEAM_GAME_STATS_COLUMNS)})
        VALUES %s
        ON CONFLICT (game_id, team_id) DO NOTHING
        """,
        rows,
    )
    conn.commit()
    cur.close()
    return len(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--season-id", default=None, help="e.g. 22021 -- omit to backfill all seasons")
    args = parser.parse_args()

    conn = get_connection()
    dates = fetch_dates_needing_backfill(conn, season_id=args.season_id)
    print(f"{len(dates)} date(s) need backfill" + (f" (season {args.season_id})" if args.season_id else " (all seasons)") + ".")

    total_inserted = 0
    failures = []

    for i, (game_date, season_id) in enumerate(dates, start=1):
        date_str = game_date.isoformat() if hasattr(game_date, "isoformat") else str(game_date)
        try:
            games = get_completed_games_with_home_away(date_str)
        except Exception as e:
            print(f"[{i}/{len(dates)}] {date_str}: FAILED to fetch scoreboard — {e}")
            failures.append((date_str, f"scoreboard: {e}"))
            time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)
            continue

        date_inserted = 0
        for g in games:
            try:
                box = boxscoretraditionalv3.BoxScoreTraditionalV3(game_id=g["game_id"])
                team_stats = box.get_data_frames()[2]  # confirmed frame index, 8/10/26

                cleaned = clean_team_boxscore(
                    team_stats, g["game_id"], date_str, season_id,
                    g["home_team_id"], g["visitor_team_id"],
                )[TEAM_GAME_STATS_COLUMNS]

                n = load_team_game_stats(cleaned, conn)
                date_inserted += n
            except Exception as e:
                failures.append((g["game_id"], str(e)))
                conn.rollback()  # same lesson as load_daily_game_logs.py -- don't let one bad game poison the rest

            time.sleep(SLEEP_SECONDS_BETWEEN_CALLS)

        total_inserted += date_inserted
        print(f"[{i}/{len(dates)}] {date_str}: {len(games)} game(s), {date_inserted} rows inserted")

    conn.close()

    print(f"\nDone. Inserted {total_inserted} total rows across {len(dates)} date(s).")
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for ref, err in failures:
            print(f"  - {ref}: {err}")
        print("\nRerun the same command to retry -- ON CONFLICT DO NOTHING means")
        print("already-succeeded games won't be touched, only genuinely missing ones.")


if __name__ == "__main__":
    main()
