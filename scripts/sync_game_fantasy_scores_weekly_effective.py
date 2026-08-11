"""
Python wrapper around sync_game_fantasy_scores_weekly_effective.sql, so
load_daily_game_logs.py (and, if you want, backfill_missing_players.py /
backfill_single_player.py) can call this directly on an existing
connection instead of shelling out to psql. The actual sync logic lives
in the .sql file -- this just runs it and reports how many rows landed.

Run standalone: python scripts/sync_game_fantasy_scores_weekly_effective.py
"""

from pathlib import Path

from db_connection import get_connection

SQL_PATH = Path(__file__).parent.parent / "schema" / "sync_game_fantasy_scores_weekly_effective.sql"


def sync_game_fantasy_scores_weekly_effective(conn) -> int:
    before = _row_count(conn)
    sql_text = SQL_PATH.read_text()

    # The file has one INSERT statement followed by verification SELECTs --
    # only the INSERT needs executing here (the SELECTs are for manual
    # psql runs); split on the first "-- ====" divider to isolate it.
    insert_sql = sql_text.split("-- =========================")[0]

    cur = conn.cursor()
    cur.execute(insert_sql)
    conn.commit()
    cur.close()

    after = _row_count(conn)
    return after - before


def _row_count(conn) -> int:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM game_fantasy_scores_weekly_effective")
    n = cur.fetchone()[0]
    cur.close()
    return n


def main():
    conn = get_connection()
    n = sync_game_fantasy_scores_weekly_effective(conn)
    conn.close()
    print(f"Synced {n} new row(s) into game_fantasy_scores_weekly_effective.")


if __name__ == "__main__":
    main()
