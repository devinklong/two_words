"""Warns when the NBA schedule in the database has GROWN since the last check.

Why: game_fantasy_scores_weekly_effective stores "games remaining in the week" and the sync only
inserts missing rows, never updates. When the NBA publishes more games (the NBA Cup knockout
games in December), rows already stored for those weeks are too low until they are rebuilt.

Each run records the schedule size in nba_schedule_count_log (only when it changed) and compares it
with the previous record. It never fails the daily run; read the output in the log.

Usage (from ~/projects/two_words):
    python scripts/check_schedule_growth.py --season-id 22026
    python scripts/check_schedule_growth.py --season-id 22026 --no-write    (look, record nothing)
"""
import argparse

from db_connection import get_connection

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS nba_schedule_count_log (
    checked_at    timestamptz NOT NULL DEFAULT now(),
    season_id     varchar     NOT NULL,
    total_games   integer     NOT NULL,
    min_per_team  integer     NOT NULL,
    max_per_team  integer     NOT NULL
)
"""

COUNTS_SQL = """
SELECT (SELECT COUNT(DISTINCT game_id) FROM team_schedule WHERE season_id = %(s)s),
       COALESCE(MIN(n), 0), COALESCE(MAX(n), 0), COUNT(*)
FROM (SELECT team_id, COUNT(*) AS n FROM team_schedule WHERE season_id = %(s)s GROUP BY team_id) t
"""

LAST_SQL = """
SELECT total_games, checked_at FROM nba_schedule_count_log
WHERE season_id = %s ORDER BY checked_at DESC LIMIT 1
"""

REBUILD = (
    "    psql -d postgres -c \"DELETE FROM game_fantasy_scores_weekly_effective WHERE game_date >= '2026-11-30'\"\n"
    "    python scripts/sync_game_fantasy_scores_weekly_effective.py\n"
    "  (adjust the date to the earliest week that contains a newly added game)"
)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season-id", required=True)
    ap.add_argument("--no-write", action="store_true", help="do not record this check")
    args = ap.parse_args()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(CREATE_SQL)
    cur.execute(COUNTS_SQL, {"s": args.season_id})
    total, lo, hi, teams = cur.fetchone()
    cur.execute(LAST_SQL, (args.season_id,))
    last = cur.fetchone()

    print(f"Schedule {args.season_id}: {total} games, {lo}-{hi} per team of 82 ({teams} teams).")
    changed = last is None or last[0] != total
    if last is None:
        print("  First check: recorded as the baseline.")
    elif total > last[0]:
        print(f"  WARN: schedule grew by {total - last[0]} games since {last[1]:%Y-%m-%d} ({last[0]} -> {total}).")
        print("  Stored 'games remaining in week' rows for the affected weeks are now too low. Rebuild them:")
        print(REBUILD)
    elif total < last[0]:
        print(f"  WARN: schedule SHRANK by {last[0] - total} games since {last[1]:%Y-%m-%d} ({last[0]} -> {total}). "
              "Check the schedule loader output.")
    else:
        print("  Unchanged since the last change.")

    if changed and not args.no_write:
        cur.execute("INSERT INTO nba_schedule_count_log (season_id, total_games, min_per_team, max_per_team) "
                    "VALUES (%s, %s, %s, %s)", (args.season_id, total, lo, hi))
    conn.commit()
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
